"""Deterministic binary-mask geometry measurements."""
from __future__ import annotations

import numpy as np
from scipy import ndimage


def topology(mask: np.ndarray) -> dict[str, int]:
    """Return 8-connected foreground components and 4-connected enclosed holes."""
    foreground = np.asarray(mask, dtype=bool)
    _, components = ndimage.label(foreground, structure=np.ones((3, 3), dtype=np.uint8))
    background = ~foreground
    labels, _ = ndimage.label(background, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8))
    border_labels = set(np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])).tolist()))
    holes = sum(1 for label_id in np.unique(labels) if label_id != 0 and label_id not in border_labels)
    return {"components": int(components), "holes": int(holes)}


def minimum_width(mask: np.ndarray) -> float | None:
    """Estimate minimum local thickness as twice the EDT radius at ridge maxima."""
    foreground = np.asarray(mask, dtype=bool)
    if not np.any(foreground):
        return None
    distance = ndimage.distance_transform_edt(foreground)
    ridges = foreground & (distance == ndimage.maximum_filter(distance, size=3, mode="constant"))
    radii = distance[ridges]
    if radii.size == 0:
        return None
    return float(2.0 * radii.min())


def skeleton_graph(mask: np.ndarray) -> dict[str, int] | None:
    """Count endpoints and branch clusters in a morphological skeleton."""
    foreground = np.asarray(mask, dtype=bool)
    if not np.any(foreground):
        return None
    structure = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
    current = foreground.copy()
    skeleton = np.zeros_like(foreground)
    for _ in range(max(foreground.shape)):
        opened = ndimage.binary_dilation(ndimage.binary_erosion(current, structure=structure), structure=structure)
        skeleton |= current & ~opened
        current = ndimage.binary_erosion(current, structure=structure)
        if not np.any(current):
            break
    neighborhood = np.ones((3, 3), dtype=np.uint8)
    neighbors = ndimage.convolve(skeleton.astype(np.uint8), neighborhood, mode="constant") - skeleton
    endpoints = int(np.count_nonzero(skeleton & (neighbors == 1)))
    junction_pixels = skeleton & (neighbors >= 3)
    _, branches = ndimage.label(junction_pixels, structure=neighborhood)
    return {"endpoints": endpoints, "branch_points": int(branches)}


def hu_shape_distance(candidate: np.ndarray, reference: np.ndarray) -> float | None:
    """Compare translation/scale/rotation-normalized Hu moments of two masks."""
    def descriptor(mask: np.ndarray) -> np.ndarray | None:
        ys, xs = np.nonzero(mask)
        if len(xs) < 3:
            return None
        x = xs.astype(np.float64); y = ys.astype(np.float64)
        x -= x.mean(); y -= y.mean()
        moments = {(p, q): float(np.sum((x ** p) * (y ** q))) for p in range(4) for q in range(4 - p)}
        m00 = moments[(0, 0)]
        eta = {(p, q): moments[(p, q)] / (m00 ** (1 + (p + q) / 2))
               for p in range(4) for q in range(4 - p) if 2 <= p + q <= 3}
        n20, n02, n11 = eta[(2, 0)], eta[(0, 2)], eta[(1, 1)]
        n30, n12, n21, n03 = eta[(3, 0)], eta[(1, 2)], eta[(2, 1)], eta[(0, 3)]
        h1 = n20 + n02
        h2 = (n20 - n02) ** 2 + 4 * n11 ** 2
        h3 = (n30 - 3*n12) ** 2 + (3*n21 - n03) ** 2
        h4 = (n30 + n12) ** 2 + (n21 + n03) ** 2
        h5 = ((n30 - 3*n12)*(n30+n12)*((n30+n12)**2-3*(n21+n03)**2)
              + (3*n21-n03)*(n21+n03)*(3*(n30+n12)**2-(n21+n03)**2))
        h6 = ((n20-n02)*((n30+n12)**2-(n21+n03)**2)
              + 4*n11*(n30+n12)*(n21+n03))
        h7 = ((3*n21-n03)*(n30+n12)*((n30+n12)**2-3*(n21+n03)**2)
              - (n30-3*n12)*(n21+n03)*(3*(n30+n12)**2-(n21+n03)**2))
        values = np.asarray([h1, h2, h3, h4, h5, h6, h7], dtype=np.float64)
        return -np.sign(values) * np.log10(np.maximum(np.abs(values), 1e-30))

    first = descriptor(np.asarray(candidate, dtype=bool))
    second = descriptor(np.asarray(reference, dtype=bool))
    if first is None or second is None:
        return None
    return float(np.linalg.norm(first - second))


def roi_edge_metrics(image_rgb: np.ndarray, mask: np.ndarray) -> dict[str, float | None]:
    """Return deterministic Tenengrad sharpness and mean mask-boundary gradient."""
    region = np.asarray(mask, dtype=bool)
    if not np.any(region):
        return {"tenengrad": None, "boundary_gradient": None}
    rgb = np.asarray(image_rgb, dtype=np.float64)
    gray = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    gx = ndimage.sobel(gray, axis=1, mode="nearest") / 8.0
    gy = ndimage.sobel(gray, axis=0, mode="nearest") / 8.0
    magnitude = np.hypot(gx, gy)
    tenengrad = float(np.mean((gx[region] ** 2) + (gy[region] ** 2)))
    boundary = region & ~ndimage.binary_erosion(region, structure=np.ones((3, 3), dtype=bool), border_value=0)
    edge = float(np.mean(magnitude[boundary])) if np.any(boundary) else None
    return {"tenengrad": tenengrad, "boundary_gradient": edge}


def compare_masks(candidate: np.ndarray, reference: np.ndarray) -> dict[str, float | None]:
    """Compare binary masks in the shared pixel coordinate system."""
    first = np.asarray(candidate, dtype=bool)
    second = np.asarray(reference, dtype=bool)
    if first.shape != second.shape:
        raise ValueError("candidate and reference masks must have identical dimensions")
    area_a = int(np.count_nonzero(first))
    area_b = int(np.count_nonzero(second))
    if not area_a or not area_b:
        return {"boundary_distance_px": None, "iou": None, "dice": None}
    intersection = int(np.count_nonzero(first & second))
    union = int(np.count_nonzero(first | second))
    boundary_a = first & ~ndimage.binary_erosion(first, structure=np.ones((3, 3), dtype=bool), border_value=0)
    boundary_b = second & ~ndimage.binary_erosion(second, structure=np.ones((3, 3), dtype=bool), border_value=0)
    distance_to_boundary_b = ndimage.distance_transform_edt(~boundary_b)
    distance_to_boundary_a = ndimage.distance_transform_edt(~boundary_a)
    max_distance = max(float(distance_to_boundary_b[boundary_a].max()), float(distance_to_boundary_a[boundary_b].max()))
    return {"boundary_distance_px": max_distance, "iou": intersection / union,
            "dice": 2.0 * intersection / (area_a + area_b)}
