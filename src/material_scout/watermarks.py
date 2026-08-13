from __future__ import annotations

from dataclasses import dataclass


MARK_KINDS = {"platform", "creator", "stock", "brand", "unknown"}
MARK_AUTHORIZATIONS = {"unknown", "allowed", "forbidden"}
MARK_TREATMENTS = {"preserve", "clean-master", "crop", "mask", "inpaint"}
PRODUCTION_RIGHTS = {"owned", "licensed", "permission", "creative-commons", "public-domain"}


@dataclass(frozen=True, slots=True)
class TreatmentDecision:
    allowed: bool
    reason: str


def decide_mark_treatment(
    rights_status: str,
    authorization: str,
    treatment: str,
) -> TreatmentDecision:
    if treatment == "preserve":
        return TreatmentDecision(True, "Preserving a visible mark does not alter attribution.")
    if treatment == "clean-master":
        if rights_status in PRODUCTION_RIGHTS:
            return TreatmentDecision(True, "Replacing with an authorized clean master is preferred.")
        return TreatmentDecision(False, "A clean master cannot be used until publication rights are recorded.")
    if rights_status not in PRODUCTION_RIGHTS:
        return TreatmentDecision(False, "Mark alteration requires recorded publication rights.")
    if authorization != "allowed":
        return TreatmentDecision(False, "Mark alteration requires explicit permission to modify that mark.")
    return TreatmentDecision(
        True,
        "Authorized treatment may create a derivative; keep the original and record the operation.",
    )
