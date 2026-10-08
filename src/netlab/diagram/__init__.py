"""ASCII topology diagrams for scenarios (deterministic, dependency-free).

Renders a scenario's declarative topology as a box-and-cable drawing:

- one box per device — hostname and device kind — in declaration order,
- one cable row per link: a drop from each endpoint's box centre, joined by a
  horizontal run; junction glyphs (``├ ┤ ┼ └ ┘ ┬``) mark turns, shared
  trunks and crossings, so links that pass over another device's drop render
  as a crossing rather than a false connection,
- a cabling legend mapping every ``HOST:Interface`` pair below the drawing.

The output is plain text: no trailing whitespace, deterministic for a given
topology, and independent of terminal width (lab topologies are small — the
drawing assumes it fits the terminal).
"""

from __future__ import annotations

from netlab.scenarios.schema import TopologySpec

GAP = 2  # columns between adjacent device boxes
PADDING = 2  # horizontal padding inside a box


def _junction(up: bool, down: bool, left: bool, right: bool) -> str:
    """The box-drawing glyph for a cell's four connected directions."""
    if up and down and left and right:
        return "┼"
    if up and down and left:
        return "┤"
    if up and down and right:
        return "├"
    if down and left and right:
        return "┬"
    if up and left and right:
        return "┴"
    if up and right:
        return "└"
    if up and left:
        return "┘"
    if down and right:
        return "┌"
    if down and left:
        return "┐"
    if up or down:
        return "│"
    if left or right:
        return "─"
    return " "


def _split_ref(ref: str) -> tuple[str, str]:
    """``R1:Gi0/0/0`` → ``("R1", "Gi0/0/0")``."""
    host, _sep, interface = ref.rpartition(":")
    return host, interface


def render_diagram(topology: TopologySpec) -> str:
    """Render ``topology`` as an ASCII diagram; see module docstring.

    Raises :class:`ValueError` when a link references an unknown device —
    the scenario loader validates links before this point, so reaching the
    raise means an unvalidated topology was passed in.
    """
    devices = topology.devices
    links = topology.links

    # Geometry: one row of equal-width boxes, in declaration order.
    inner = max(max(len(d.name), len(d.type.value)) for d in devices) + PADDING
    box_width = inner + 2
    step = box_width + GAP
    centers: dict[str, int] = {}
    connected: set[str] = set()
    for index, device in enumerate(devices):
        centers[device.name] = index * step + 1 + inner // 2
    total = len(devices) * step - GAP

    # Resolve links to (left, right) column spans; validate endpoint devices.
    spans: list[tuple[int, int]] = []
    for link in links:
        ends: list[int] = []
        for ref in (link.source.ref, link.destination.ref):
            host, _interface = _split_ref(ref)
            if host not in centers:
                raise ValueError(
                    f"diagram: link endpoint {ref!r} references unknown device"
                )
            ends.append(centers[host])
            connected.add(host)
        spans.append((min(ends), max(ends)))

    rows: list[str] = []

    # Device boxes (top border, name, kind, bottom border with cable stubs).
    rows.append("  ".join("┌" + "─" * inner + "┐" for _ in devices))
    rows.append("  ".join(f"│{d.name.center(inner)}│" for d in devices))
    rows.append("  ".join(f"│{d.type.value.center(inner)}│" for d in devices))

    bottom = [" "] * total
    for index, device in enumerate(devices):
        left = index * step
        bottom[left] = "└"
        for offset in range(1, box_width - 1):
            bottom[left + offset] = "─"
        bottom[left + box_width - 1] = "┘"
        if device.name in connected:
            bottom[centers[device.name]] = "┬"
    rows.append("".join(bottom))

    # Cable rows: link N is drawn at depth N. Every endpoint's drop runs from
    # its box bottom down to its deepest link's row (drops from the same box
    # share one column, like a trunk); each row carries one horizontal run.
    # drop_rows is computed first so earlier rows can see deeper drops.
    box_bottom = 3
    drop_rows: dict[int, int] = {}
    for depth, (left, right) in enumerate(spans):
        cable_row = box_bottom + 1 + depth
        for col in (left, right):
            drop_rows[col] = max(drop_rows.get(col, -1), cable_row)

    for depth, (left, right) in enumerate(spans):
        cable_row = box_bottom + 1 + depth
        cells = set(range(left, right + 1)) | {
            col for col, deepest in drop_rows.items() if deepest >= cable_row
        }
        line = [" "] * total
        for col in cells:
            # Drops are contiguous from the box bottom, so a drop covering
            # this row always continues from above; it continues below only
            # when the column's deepest link is below this row. Horizontal
            # arms exist only inside this row's span.
            deepest = drop_rows.get(col, -1)
            vertical = deepest >= cable_row
            inside = left <= col <= right
            line[col] = _junction(
                vertical,
                vertical and deepest > cable_row,
                inside and col > left,
                inside and col < right,
            )
        rows.append("".join(line).rstrip())

    if links:
        rows.append("")
        rows.append("Links:")
        for link in links:
            source = _split_ref(link.source.ref)
            destination = _split_ref(link.destination.ref)
            rows.append(f"  {source[0]} {source[1]} ↔ {destination[0]} {destination[1]}")
    return "\n".join(rows)


__all__ = ["render_diagram"]
