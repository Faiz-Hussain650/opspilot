"""Load test for the OpsPilot API.

    make load-test                     # opens the Locust UI on http://localhost:8089
    uvx locust -f loadtest/locustfile.py --host http://localhost:8000 \
        --headless -u 5 -r 1 -t 2m     # headless, 5 users for 2 minutes

Keep the user count low when running against a local 7B model - each uncached
question keeps the GPU busy for several seconds. Most of the traffic below
repeats popular questions, so you can watch the semantic cache take over in Grafana.
"""

from __future__ import annotations

import random

from locust import HttpUser, between, task

POPULAR = [
    "checkout is returning 502 with ERR_POOL_TIMEOUT",
    "checkout-service 502 errors, connection pool timeout in the logs",
    "redis OOM command not allowed on the cart cache",
    "payflow timeouts, should we fail over to cardbridge?",
    "pod in CrashLoopBackOff after deploy",
]

LONG_TAIL = [
    "consumer lag on one partition of order-events",
    "users getting 401 kid not found after key rotation",
    "postgres disk almost full on the primary",
    "certificate expired on the storefront",
    "how do I abort a canary rollout",
]


class OnCallEngineer(HttpUser):
    wait_time = between(2, 6)

    @task(6)
    def popular_question(self):
        self._ask(random.choice(POPULAR))

    @task(2)
    def long_tail_question(self):
        self._ask(random.choice(LONG_TAIL))

    @task(1)
    def stats(self):
        self.client.get("/stats", name="/stats")

    def _ask(self, question: str):
        with self.client.post(
            "/chat",
            json={"question": question, "use_cache": True},
            name="/chat",
            timeout=300,
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
            elif not resp.json().get("answer"):
                resp.failure("empty answer")
