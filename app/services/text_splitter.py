"""Split input text into fixed-size line chunks without line reuse."""


def split_into_chunks(text: str, lines_per_chunk: int = 15) -> list[str]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    chunks: list[str] = []
    for i in range(0, len(lines), lines_per_chunk):
        chunk_lines = lines[i : i + lines_per_chunk]
        chunks.append("\n".join(chunk_lines))
    return chunks


def distribute_chunks(chunks: list[str], session_ids: list[int]) -> list[tuple[int, int, str]]:
    """
    Assign chunks to sessions round-robin.
    Returns list of (session_id, chunk_index, chunk_text).
    Each line appears in exactly one chunk; chunks are not reused.
    """
    if not session_ids:
        raise ValueError("At least one session is required")

    assignments: list[tuple[int, int, str]] = []
    for idx, chunk in enumerate(chunks):
        session_id = session_ids[idx % len(session_ids)]
        assignments.append((session_id, idx, chunk))
    return assignments
