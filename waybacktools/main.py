from datetime import datetime
from logging import getLogger, StreamHandler, Formatter, DEBUG, INFO
from pathlib import Path
import re
from threading import Thread
import time
from traceback import format_exc
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests
from playwright.sync_api import sync_playwright

from .exceptions import *


class waybackmachine:
    def __init__(
        self,
        max_tries: int = 5,
        browser_type: str = "chromium",
        user_agent: str = "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
        proxies: dict = {},
        debug: bool = False,
    ) -> None:
        self.logger = getLogger("Wayback")
        if debug == True:
            self.logger.setLevel(DEBUG)
        else:
            self.logger.setLevel(INFO)
        handler = StreamHandler()
        formatter = Formatter("[%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

        self.user_agent = user_agent
        self.proxies = proxies
        self.max_tries = max_tries

        if browser_type not in ["chromium", "firefox", "webkit"]:
            raise ValueError("browser_type should be 'chromium', 'firefox' or 'webkit'")

        self.browser_type = browser_type
        self.playwright = sync_playwright().start()
        self.browser = getattr(self.playwright, self.browser_type).launch()
        self.logger.debug("Browser launch")

    def save(self, url: str, show_resources: bool = True, max_tries: int = None) -> str:
        if max_tries is None:
            max_tries = self.max_tries

        archive_data = {"url": None, "timestamp": None, "resources": None}

        def archive_save() -> None:
            for i in range(max_tries):
                try:
                    r = requests.get(
                        "https://web.archive.org/save/" + url, proxies=self.proxies
                    )

                    if r.status_code == 429:
                        raise TooManyRequestsError(
                            "Your IP has been blocked."
                            "Save Page Now has a limit of 15 requests per minute."
                            "Please try again in 5 minutes."
                        )

                    archive_data["url"] = r.url
                except Exception as e:
                    self.logger.debug(f"Attempt {i + 1} failed\n{format_exc()}")
                    if i + 1 == max_tries:
                        raise RetryLimitExceededError(
                            f"The retry limit has been reached.\n{format_exc()}"
                        )

        def get_resources() -> None:
            def get_status(job_id: str) -> dict:
                status_r = requests.get(
                    "https://web.archive.org/save/status/" + job_id,
                    proxies=self.proxies,
                )
                return status_r.json()

            for i in range(max_tries):
                try:
                    data = {"url": url, "capture_all": "on"}

                    headers = {
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng",
                        "Content-Type": "application/x-www-form-urlencoded",
                    }

                    r = requests.post(
                        "https://web.archive.org/save/" + url,
                        headers=headers,
                        data=data,
                        proxies=self.proxies,
                    )
                    soup = BeautifulSoup(r.text, "html.parser")

                    job_id = None
                    for script in soup.find_all("script"):
                        if script.string:
                            matches = re.findall(
                                r'spn\.watchJob\("([^"]+)",', script.string
                            )
                            if matches:
                                job_id = matches[0]
                                break

                    if job_id:
                        if show_resources:
                            self.logger.debug(f"job_id is {job_id}")
                            status, old_resources = "pending", []
                            while status == "pending":
                                status_data = get_status(job_id)
                                new_resources = status_data.get("resources", [])
                                if new_resources != old_resources:
                                    for c in set(new_resources) - set(old_resources):
                                        self.logger.info(c)
                                old_resources = new_resources
                                status = status_data.get("status", "pending")
                                for i in range(30):
                                    if (
                                        archive_data["url"] is None
                                        or status == "pending"
                                    ):
                                        time.sleep(0.1)
                                    else:
                                        status_data = get_status(job_id)
                                        new_resources = status_data.get("resources", [])
                                        if new_resources != old_resources:
                                            for c in set(new_resources) - set(
                                                old_resources
                                            ):
                                                self.logger.info(c)
                        else:
                            while archive_data["url"] is None:
                                time.sleep(0.1)

                        final_status = get_status(job_id)
                        archive_data["timestamp"] = final_status.get("timestamp", [])
                        archive_data["resources"] = final_status.get("resources", [])
                    else:
                        self.logger.debug(f"job_id not found")
                except Exception as e:
                    self.logger.debug(f"Attempt {i + 1} failed\n{format_exc()}")
                    if i + 1 == max_tries:
                        raise RetryLimitExceededError(
                            f"The retry limit has been reached.\n{format_exc()}"
                        )

        thread1 = Thread(target=archive_save)
        thread2 = Thread(target=get_resources)
        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

        return archive_data

    def get(
        self, url: str, timestamp: datetime | str = "latest", max_tries: int = None
    ) -> tuple:
        if max_tries is None:
            max_tries = self.max_tries

        if timestamp not in ["latest", "oldest"] and not isinstance(
            timestamp, datetime
        ):
            raise ValueError(
                "timestamp should be 'latest', 'oldest', or a datetime object"
            )

        for i in range(max_tries):
            try:
                timestamp = (
                    datetime.now()
                    if timestamp == "latest"
                    else (
                        datetime(2001, 10, 24, 0, 0, 0)
                        if timestamp == "oldest"
                        else timestamp
                    )
                )
                timestamp_str = timestamp.strftime("%Y%m%d%H%M%S")

                params = {
                    "url": url,
                    "timestamp": timestamp_str,
                }

                r = requests.get(
                    "https://archive.org/wayback/available",
                    params=params,
                    proxies=self.proxies,
                )

                archive = r.json()["archived_snapshots"].get("closest")
                if archive:
                    archive_url, archive_timestamp = (
                        archive["url"],
                        archive["timestamp"],
                    )
                    archive_timestamp = datetime.strptime(
                        archive_timestamp, "%Y%m%d%H%M%S"
                    )
                    return (archive_url, archive_timestamp)
                else:
                    return ()
            except Exception as e:
                self.logger.debug(f"Attempt {i + 1} failed\n{format_exc()}")

                if i + 1 == max_tries:
                    raise RetryLimitExceededError(
                        f"The retry limit has been reached.\n{format_exc()}"
                    )

    def download(
        self,
        url: str,
        path: str | None = None,
        ext: str = "mhtml",
        max_tries: int = None,
    ) -> str:
        if max_tries is None:
            max_tries = self.max_tries

        if ext not in ["mhtml", "mht", "pdf"]:
            raise ValueError("ext should be 'mhtml', 'mht' or 'pdf'")

        for i in range(max_tries):
            try:
                self.logger.debug(f"url:{url}")

                if "web.archive.org/web/" not in url:
                    archive_data = self.get(url, max_tries=1)
                    if archive_data:
                        archive_url = archive_data[0]
                        self.logger.debug(f"Archive Found")
                    else:
                        raise NotFoundError("Archive Not Found")
                else:
                    archive_url = url

                playwright_proxy = {}
                if self.proxies:
                    scheme = urlparse(archive_url).scheme
                    proxy = self.proxies.get(scheme)
                    if proxy:
                        playwright_proxy = {"server": proxy}
                        if "@" in proxy:
                            auth, ip = proxy.split("@")
                            playwright_proxy["server"] = "http://" + ip
                            username, password = auth.split("//")[1].split(":")
                            playwright_proxy["username"] = username
                            playwright_proxy["password"] = password

                launch_options = {"headless": True}
                if playwright_proxy:
                    launch_options["proxy"] = playwright_proxy

                page = self.browser.new_page()
                self.logger.debug(f"Access to {archive_url}")
                page.goto(archive_url, wait_until="domcontentloaded")

                if path is None:
                    timestamp = re.search(
                        r"web\.archive\.org/web/(\d+)/", archive_url
                    ).group(1)
                    path = f"{page.title()} - {timestamp}.{ext}"

                if not path.endswith(ext):
                    path += f".{ext}"

                if ext == "mhtml":
                    client = page.context.new_cdp_session(page)
                    mhtml = client.send("Page.captureSnapshot")["data"]
                    with open(path, mode="w", encoding="UTF-8", newline="\n") as file:
                        file.write(mhtml)
                elif ext == "pdf":
                    page.pdf(path=path)

                page.close()
                self.logger.debug(f"Page close")

                absolute_path = Path(path).resolve()
                return absolute_path

            except Exception as e:
                self.logger.debug(f"Attempt {i + 1} failed\n{format_exc()}")
                if i + 1 == max_tries:
                    raise RetryLimitExceededError(
                        f"The retry limit has been reached.\n{format_exc()}"
                    )
