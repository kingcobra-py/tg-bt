import logging
import re
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Markers that indicate a real bot result (checked in Status OR Response)
VALID_RESULT_MARKERS = (
    "charged",
    "declined",
    "approved",
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
    parts = NUMBERED_BLOCK_RE.split(text)
    blocks: list[str] = []
    if parts and not NUMBERED_BLOCK_RE.search(text):
        blocks.append(text)
    else:
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
    if not blocks:
        blocks = [text]
    return blocks


def parse_results(text: str) -> tuple[list[ParsedResult], list[str]]:
    """Parse bot response into valid results and failed/unmatched blocks."""
    valid: list[ParsedResult] = []
    failed: list[str] = []

    for raw_block in _split_blocks(text):
        block = _strip_block(raw_block)
        if not block:
            continue

        if ANTISPAM_RE.search(block):
            continue

        match = RESULT_BLOCK_RE.search(block)
        if match:
            result = ParsedResult(
                cc=match.group("cc").strip(),
                status=match.group("status").strip(),
                response=match.group("response").strip(),
                receipt=(match.group("receipt") or "").strip(),
                raw=block,
            )
            if result.is_valid():
                valid.append(result)
            else:
                failed.append(block)
        elif "CC :" in block or "Status :" in block:
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
