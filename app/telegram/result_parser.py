import logging
import re
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Markers that indicate a real bot result (checked in Status OR Response)
VALID_RESULT_MARKERS = (
    "charged",
    "declined",
    "approved",
    "generic_decline",
    "incorrect_cvc",
    "insufficient_funds",
    "expired_card",
    "incorrect_number",
    "processing_error",
    "card_declined",
    "invalid_cvc",
    "invalid_expiry",
    "authentication_required",
    "live",
    "dead",
    "error",
)

# Valid result block must contain CC, Status, Response
RESULT_BLOCK_RE = re.compile(
    r"CC\s*:\s*(?P<cc>[^\n]+)\s*\n"
    r"Status\s*:\s*(?P<status>[^\n]+)\s*\n"
    r"Response\s*:\s*(?P<response>[^\n]+)"
    r"(?:\s*\nReceipt\s*:\s*(?P<receipt>[^\n]+))?",
    re.IGNORECASE,
)

COMPLETION_RE = re.compile(
    r"Took:\s*[\d.]+\s*s\s*\|\s*Proxy\s*:\s*Live\s*⛅️?\s*\n?\s*User\s*:\s*\S+\s*$",
    re.IGNORECASE | re.MULTILINE,
)

COMPLETION_FOOTER_RE = re.compile(
    r"Took:\s*[\d.]+\s*s\s*\|\s*Proxy\s*:\s*Live\s*⛅️?\s*\n?\s*User\s*:\s*\S+.*$",
    re.IGNORECASE | re.DOTALL,
)

NUMBERED_BLOCK_RE = re.compile(r"(?:^|\n)(\d+\=\")", re.MULTILINE)

ANTISPAM_RE = re.compile(
    r"AntiSpam\s*Alert|Please\s*Try\s*Again\s*After\s*(\d+)\s*Seconds?",
    re.IGNORECASE,
)

# Raw input card lines echoed by bot — not failures
INPUT_CARD_RE = re.compile(r"^\d{12,19}\|\d{1,2}\|\d{2,4}\|\d{3,4}$")


@dataclass
class ParsedResult:
    cc: str
    status: str
    response: str
    receipt: str = ""
    raw: str = ""

    def is_valid(self) -> bool:
        if not self.cc or not self.status or not self.response:
            return False
        cc_parts = self.cc.strip().split("|")
        if len(cc_parts) < 4:
            return False
        # Valid when Status OR Response contains a known result marker (e.g. incorrect_cvc, charged)
        combined = f"{self.status} {self.response}".lower()
        return any(marker in combined for marker in VALID_RESULT_MARKERS)

    def format_message(self) -> str:
        lines = [
            f"CC : {self.cc.strip()}",
            f"Status : {self.status.strip()}",
            f"Response : {self.response.strip()}",
        ]
        if self.receipt:
            lines.append(f"Receipt : {self.receipt.strip()}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


def _strip_block(block: str) -> str:
    block = block.strip()
    block = re.sub(r'^\d+\="?\s*', "", block)
    block = block.rstrip('"').strip()
    block = COMPLETION_FOOTER_RE.sub("", block).strip()
    return block


def _split_blocks(text: str) -> list[str]:
    """Split bot output into individual result blocks."""
    if NUMBERED_BLOCK_RE.search(text):
        parts = NUMBERED_BLOCK_RE.split(text)
        blocks: list[str] = []
        i = 0
        while i < len(parts):
            if re.match(r"\d+\=\"", parts[i]):
                content = parts[i + 1] if i + 1 < len(parts) else ""
                blocks.append(parts[i] + content)
                i += 2
            else:
                if parts[i].strip():
                    blocks.append(parts[i])
                i += 1
        return blocks if blocks else [text]

    # Split on each new CC : block (blank line or direct CC start)
    parts = re.split(r"(?:\n{2,}|(?=\nCC\s*:))", text)
    blocks = [p.strip() for p in parts if p.strip()]
    return blocks if blocks else [text]


def parse_results(text: str) -> tuple[list[ParsedResult], list[str]]:
    """Parse bot response into valid results and failed/unmatched blocks."""
    valid: list[ParsedResult] = []
    failed: list[str] = []
    seen_cc: set[str] = set()

    for raw_block in _split_blocks(text):
        block = _strip_block(raw_block)
        if not block:
            continue

        if ANTISPAM_RE.search(block):
            continue

        if INPUT_CARD_RE.match(block.replace(" ", "")):
            continue

        if block.lower().startswith("processing") or block.lower().startswith("please wait"):
            continue

        matched = False
        for match in RESULT_BLOCK_RE.finditer(block):
            matched = True
            result = ParsedResult(
                cc=match.group("cc").strip(),
                status=match.group("status").strip(),
                response=match.group("response").strip(),
                receipt=(match.group("receipt") or "").strip(),
                raw=match.group(0),
            )
            if result.cc in seen_cc:
                continue
            seen_cc.add(result.cc)
            if result.is_valid():
                valid.append(result)
            else:
                failed.append(match.group(0))
        if not matched and "CC :" in block and "Status :" in block:
            if block not in failed:
                failed.append(block)

    return valid, failed


def is_completion_message(text: str) -> bool:
    return bool(COMPLETION_RE.search(text))


def is_antispam_message(text: str) -> tuple[bool, int]:
    match = ANTISPAM_RE.search(text)
    if match:
        wait = int(match.group(1)) if match.lastindex and match.group(1) else 2
        return True, wait + 1
    return False, 0
