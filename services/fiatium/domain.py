import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_AMOUNT = 1_000_000_000_000  # explicit demo limit, well below BIGINT and JS safe integers
Cents = Annotated[int, Field(strict=True, gt=0, le=MAX_AMOUNT)]


class Scenario(StrEnum):
    success = "success"
    decline = "decline"
    timeout = "timeout"
    delayed = "delayed"
    duplicate = "duplicate"
    ledger_failure = "ledger_failure"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerInput(Input):
    name: str = Field(min_length=1, max_length=120)


class InvoiceInput(Input):
    customer_id: UUID
    amount: Cents
    currency: Literal["CAD"] = "CAD"


class PaymentInput(Input):
    invoice_id: UUID
    amount: Cents
    currency: Literal["CAD"] = "CAD"
    scenario: Scenario = Scenario.success


def fingerprint(payload: BaseModel) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def processor_outcome(scenario: str, attempt: int) -> str:
    if scenario == "decline":
        return "declined"
    if scenario == "timeout" and attempt == 1:
        return "timeout"
    if scenario == "delayed" and attempt == 1:
        return "authorized"
    return "settled"


class Problem(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
