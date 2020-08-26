# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------------
# Name:         sfp_onyphe
# Purpose:      SpiderFoot plug-in to check if the IP is included on Onyphe
#               data (threat list, geo-location, pastries, vulnerabilities)
#
# Author:      Filip Aleksić <faleksicdev@gmail.com>
#
# Created:     2020-08-21
# Copyright:   (c) Steve Micallef
# Licence:     GPL
# -------------------------------------------------------------------------------

from sflib import SpiderFoot, SpiderFootPlugin, SpiderFootEvent
import json
from datetime import datetime
import time


class sfp_onyphe(SpiderFootPlugin):
    """Onyphe:Footprint,Investigate,Passive:Reputation Systems:apikey:Check Onyphe data (threat list, geo-location, pastries, vulnerabilities)  about a given IP."""

    meta = {
        "name": "Onyphe",
        "summary": "Check Onyphe data (threat list, geo-location, pastries, vulnerabilities)  about a given IP.",
        "flags": ["apikey"],
        "useCases": ["Footprint", "Passive", "Investigate"],
        "categories": ["Reputation Systems"],
        "dataSource": {
            "website": "https://www.onyphe.io",
            "model": "FREE_AUTH_LIMITED",
            "references": ["https://www.onyphe.io/documentation/api"],
            "apiKeyInstructions": [
                "Visit https://www.onyphe.io/login/#register",
                "Register a free account",
                "You should receive your API key on your email, or you can get it by yourself following next steps",
                "Go to your account settings https://www.onyphe.io/auth/account",
                "The API key is listed under 'API Key'",
            ],
            "favIcon": "https://www.onyphe.io/favicon.ico",
            "logo": "https://www.onyphe.io/img/logo-solo.png",
            "description": "ONYPHE is a search engine for open-source "
            "and cyber threat intelligence data collected by crawling "
            "various sources available on the Internet or by listening "
            "to Internet background noise. They make this data available "
            "through API that we use. We check their data to see following "
            "information about the IP: geo-location, does it have some "
            "vulnerabilities, is it on some pastries (PasteBin) and "
            "is it on their threat list",
        },
    }

    opts = {
        "api_key": "",
        "paid_plan": False,
        "max_page": 10,
        "verify": True,
        "age_limit_days": 30,
    }
    optdescs = {
        "api_key": "Onyphe access token.",
        "paid_plan": "Are you using paid plan? Paid plan has pagination enabled",
        "max_page": "Maximum number of pages to iterate through. Onyphe has a maximum of 1000 pages (10,000 results). Only matters for paid plans",
        "verify": "Verify identified domains still resolve to the associated specified IP address.",
        "age_limit_days": "Ignore any records older than this many days. 0 = unlimited.",
    }

    results = None
    errorState = False

    def setup(self, sfc, userOpts=dict()):
        self.sf = sfc
        self.results = self.tempStorage()

        for opt in list(userOpts.keys()):
            self.opts[opt] = userOpts[opt]

    # What events is this module interested in for input
    def watchedEvents(self):
        return ["IP_ADDRESS", "IPV6_ADDRESS"]

    # What events this module produces
    def producedEvents(self):
        return [
            "GEOINFO",
            "MALICIOUS_IPADDR",
            "LEAKSITE_CONTENT",
            "VULNERABILITY",
            "RAW_RIR_DATA",
            "INTERNET_NAME",
            "INTERNET_NAME_UNRESOLVED",
            "PHYSICAL_COORDINATES",
        ]

    def query(self, endpoint, ip, page=1):
        retarr = list()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"apikey {self.opts['api_key']}",
        }
        res = self.sf.fetchUrl(
            f"https://www.onyphe.io/api/v2/simple/{endpoint}/{ip}?page={page}",
            timeout=self.opts["_fetchtimeout"],
            useragent=self.opts["_useragent"],
            headers=headers,
        )

        if res["code"] == "429":
            self.sf.error("Reaching rate limit on Onyphe API", False)
            self.errorState = True
            return None

        if res["code"] == 400:
            self.sf.error("Invalid request or API key on Onyphe", False)
            self.errorState = True
            return None

        try:
            info = json.loads(res["content"])
            if "status" in info and info["status"] == "nok":
                self.sf.error(
                    f"Unexpected error happened while requesting data from Onyphe. Error message: {info.get('text', '')}",
                    False,
                )
                self.errorState = True
                return None
            elif "results" not in info or info["results"] == []:
                self.sf.info(f"No Onyphe {endpoint} data found for {ip}")
                return None
        except Exception as e:
            self.sf.debug(f"{e.__class__} {res['code']} {res['content']}")
            self.sf.error("Error processing JSON response from Onyphe.", False)
            return None

        # Go through other pages if user has paid plan
        try:
            current_page = int(info["page"])
            if (
                self.opts["paid_plan"]
                and info.get("page")
                and int(info.get("max_page")) > current_page
            ):
                page = current_page + 1

                if page > self.opts["max_page"]:
                    self.sf.error(
                        "Maximum number of pages from options for Onyphe reached.",
                        False,
                    )
                    return [info]
                retarr.append(info)
                response = self.query(endpoint, ip, page)
                if response:
                    retarr.extend(response)
            else:
                retarr.append(info)

        except ValueError:
            self.sf.error(
                f"Unexpected value for page in response from Onyphe, url: https://www.onyphe.io/api/v2/simple/{endpoint}/{ip}?page={page}",
                False,
            )
            self.errorState = True
            return None

        return retarr

    def emitLocationEvent(self, location, eventData, event):
        if location is None:
            return
        self.sf.info(f"Found location for {eventData}: {location}")

        evt = SpiderFootEvent("PHYSICAL_COORDINATES", location, self.__name__, event)
        self.notifyListeners(evt)

    def emitDomainData(self, response, eventData, event):
        domains = set()
        if response.get("domain") is not None:
            domains.add(response["domain"])

        if response.get("subdomains") is not None and isinstance(
            response["subdomains"], list
        ):
            for subDomain in response["subdomains"]:
                domains.add(subDomain)

        for domain in domains:
            if self.opts["verify"] and not self.sf.resolveHost(domain):
                self.sf.debug(f"Host {domain} could not be resolved for {eventData}")
                evt = SpiderFootEvent(
                    "INTERNET_NAME_UNRESOLVED", domain, self.__name__, event
                )
                self.notifyListeners(evt)
            else:
                evt = SpiderFootEvent("INTERNET_NAME", domain, self.__name__, event)
                self.notifyListeners(evt)

    def isFreshEnough(self, result):
        limit = self.opts["age_limit_days"]
        if limit <= 0:
            return True

        timestamp = result.get("@timestamp")
        if timestamp is None:
            self.sf.debug("Record doesn't have timestamp defined")
            return False

        last_dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
        last_ts = int(time.mktime(last_dt.timetuple()))
        age_limit_ts = int(time.time()) - (86400 * limit)

        if last_ts < age_limit_ts:
            self.sf.debug("Record found but too old, skipping.")
            return False

        return True

    # Handle events sent to this module
    def handleEvent(self, event):
        eventName = event.eventType
        srcModuleName = event.module
        eventData = event.data
        sentData = set()

        if self.errorState:
            return None

        self.sf.debug("Received event, %s, from %s" % (eventName, srcModuleName))

        if self.opts["api_key"] == "":
            self.sf.error("You enabled sfp_onyphe, but did not set an API key!", False)
            self.errorState = True
            return None

        # Don't look up stuff twice
        if eventData in self.results:
            self.sf.debug("Skipping " + eventData + " as already mapped.")
            return None

        self.results[eventData] = True

        geoLocDataArr = self.query("geoloc", eventData)

        if geoLocDataArr is not None:
            evt = SpiderFootEvent(
                "RAW_RIR_DATA", str(geoLocDataArr), self.__name__, event
            )
            self.notifyListeners(evt)

            for geoLocData in geoLocDataArr:
                if self.checkForStop():
                    return None

                for result in geoLocData["results"]:
                    if not self.isFreshEnough(result):
                        continue

                    location = ", ".join(
                        [
                            _f
                            for _f in [
                                result.get("city"),
                                result.get("country"),
                            ]
                            if _f
                        ]
                    )
                    self.sf.info("Found GeoIP for " + eventData + ": " + location)

                    if location in sentData:
                        self.sf.debug(f"Skipping {location}, already sent")
                        continue

                    sentData.add(location)

                    evt = SpiderFootEvent("GEOINFO", location, self.__name__, event)
                    self.notifyListeners(evt)

                    coordinates = result.get("location")
                    if coordinates is None:
                        continue

                    if coordinates in sentData:
                        self.sf.debug(f"Skipping {coordinates}, already sent")
                        continue
                    sentData.add(coordinates)

                    self.emitLocationEvent(coordinates, eventData, event)

                    self.emitDomainData(result, eventData, event)

        pastriesDataArr = self.query("pastries", eventData)

        if pastriesDataArr is not None:
            evt = SpiderFootEvent(
                "RAW_RIR_DATA", str(pastriesDataArr), self.__name__, event
            )
            self.notifyListeners(evt)

            for pastriesData in pastriesDataArr:
                if self.checkForStop():
                    return None

                for result in pastriesData["results"]:
                    pastry = result.get("content")
                    if pastry is None:
                        continue

                    if pastry in sentData:
                        self.sf.debug(f"Skipping {pastry}, already sent")
                        continue
                    sentData.add(pastry)

                    if not self.isFreshEnough(result):
                        continue

                    evt = SpiderFootEvent(
                        "LEAKSITE_CONTENT", pastry, self.__name__, event
                    )
                    self.notifyListeners(evt)

        threatListDataArr = self.query("threatlist", eventData)

        if threatListDataArr is not None:
            evt = SpiderFootEvent(
                "RAW_RIR_DATA", str(threatListDataArr), self.__name__, event
            )
            self.notifyListeners(evt)

            for threatListData in threatListDataArr:
                if self.checkForStop():
                    return None

                for result in threatListData["results"]:
                    threatList = result.get("threatlist")

                    if threatList is None:
                        continue

                    if threatList in sentData:
                        self.sf.debug(f"Skipping {threatList}, already sent")
                        continue
                    sentData.add(threatList)

                    if not self.isFreshEnough(result):
                        continue

                    evt = SpiderFootEvent(
                        "MALICIOUS_IPADDR",
                        result.get("threatlist"),
                        self.__name__,
                        event,
                    )
                    self.notifyListeners(evt)

        vulnerabilityDataArr = self.query("vulnscan", eventData)

        if vulnerabilityDataArr is not None:
            evt = SpiderFootEvent(
                "RAW_RIR_DATA", str(vulnerabilityDataArr), self.__name__, event
            )
            self.notifyListeners(evt)

            for vulnerabilityData in vulnerabilityDataArr:
                if self.checkForStop():
                    return None

                for result in vulnerabilityData["results"]:
                    cves = result.get("cve")

                    if cves is None:
                        continue

                    cveData = ", ".join([cve for cve in cves if cve])

                    if cveData in sentData:
                        self.sf.debug(f"Skipping {cveData}, already sent")
                        continue
                    sentData.add(cveData)

                    if not self.isFreshEnough(result):
                        continue

                    evt = SpiderFootEvent(
                        "VULNERABILITY",
                        cveData,
                        self.__name__,
                        event,
                    )
                    self.notifyListeners(evt)


# End of sfp_onyphe class