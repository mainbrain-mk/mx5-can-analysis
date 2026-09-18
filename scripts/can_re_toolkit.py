"""
Referenzfreie/robuste Analysebausteine fuer CAN-Signal-Reverse-Engineering (MX-5 Projekt).

Portiert aus CSS-Electronics/can-bus-reverse-engineering-skills (common.py, MIT-Lizenz,
https://github.com/CSS-Electronics/can-bus-reverse-engineering-skills), auf reine
numpy-Arrays reduziert (kein CANsub/cantools-Signal-Objekt noetig - wir extrahieren
Rohwerte bereits selbst in can_byte_search.py / can_bitsearch.py). Ergaenzt unsere
bestehende Pearson/Spearman-Korrelation (can_byte_search.py) um drei Dinge, die dort
fehlen:

1. plausibility(): referenzfreier Score, ob eine dekodierte Rohwert-Reihe ueberhaupt wie
   ein echtes physikalisches Signal aussieht (glatt, nicht modular umlaufend).
2. detect_extreme_outliers(): agnostische Sentinel-/Ausreisser-Erkennung ("Signal
   ungueltig"-Codes), unabhaengig von Wert/Vorzeichen/Haeufigkeit.
3. propose_round_calibration() / propose_anchor_calibration(): Scale/Offset-Nachkorrektur,
   die NICHT auf R²/Spearman vertraut (die sind blind fuer kleine Scale-Fehler und
   konstanten Bias), sondern explizit den eingefuehrten systematischen Bias misst und nur
   bei kleinem Bias automatisch anwendet.

Siehe Plan/docs/logs/can-bus-status.md fuer den Kontext (Sanity-Check gegen unsere bereits
kalibrierten Formeln: FLI%, CPP_PER_MZ%, Torque%, BrakePressure, YawRate_Corr).
"""
import numpy as np
from scipy.stats import spearmanr


# ---------------------------------------------------------------------------
# Referenzfreie Plausibilitaet
# ---------------------------------------------------------------------------

def plausibility(raw_unsigned: np.ndarray, length: int) -> dict:
    """Referenzfreier Plausibilitaets-Score einer dekodierten Rohwert-Reihe.

    Ein korrekt gefundenes Feld dekodiert zu einer glatten, nicht umlaufenden
    Integer-Reihe. Ein falsch gelegtes/falsch endianes/zu breites Feld zeigt zwei
    Auffaelligkeiten: (a) MODULARE UMLAEUFE - Delta pro Frame nahe +/-2**length (das
    Feld ueberschreitet eine echte Feldgrenze), und (b) WEISSES RAUSCHEN - grosse
    Deltas relativ zur eigenen Wertespanne. Beide Metriken sind skalenfrei.

    raw_unsigned : UNSIGNED Rohwerte in Frame-Reihenfolge (nicht resampled).
    length       : Feldlaenge in Bits (definiert den Modulus 2**length).

    Rueckgabe {wrap_rate, jump_rate, ac1, score}, score in [0,1] (hoeher = plausibler).
    """
    raw = np.asarray(raw_unsigned, dtype=np.float64)
    n = raw.size
    rng = float(np.ptp(raw)) if n else 0.0
    if n < 8 or rng == 0.0:
        return {"wrap_rate": 0.0, "jump_rate": 0.0, "ac1": 0.0, "score": 0.0}
    mod = float(1 << int(length))
    d = np.diff(raw)
    wrap_rate = float(np.mean(np.abs(d) > 0.5 * mod))
    jump_rate = float(np.mean(np.abs(d) > 0.25 * rng))
    s = raw - raw.mean()
    denom = float(np.dot(s, s)) or 1.0
    ac1 = float(np.dot(s[:-1], s[1:]) / denom)
    score = max(0.0, ac1) * (1.0 - wrap_rate) * (1.0 - 0.5 * jump_rate)
    return {"wrap_rate": round(wrap_rate, 4), "jump_rate": round(jump_rate, 4),
            "ac1": round(ac1, 4), "score": round(float(np.clip(score, 0.0, 1.0)), 4)}


def scale_plausibility(scale: float, tol: float = 0.02) -> dict:
    """Wie nah liegt |scale| an einem 'OEM-hübschen' Wert? -> {nice, nearest, rel_err}.

    Nice = m*10**k (m in {1,2,2.5,5}) ODER 2**-j. Eine gefittete Scale, die NICHT nahe
    an einem huebschen Wert liegt (z.B. -1/2593), deutet meist auf eine falsche
    Feldgeometrie hin. NUR eine Einbahn-Heuristik: huebsch beweist nicht, dass die
    Breite stimmt (ein zu breit gelesenes Feld einer Binaerbruch-Scale 2**-j bleibt
    ebenfalls huebsch) - das faengt stattdessen die Parsimonie-Regel in can_bitsearch.py.
    """
    a = abs(float(scale))
    if a == 0.0 or not np.isfinite(a):
        return {"nice": False, "nearest": 0.0, "rel_err": float("inf")}
    cands = [m * 10.0 ** k for k in range(-12, 7) for m in (1.0, 2.0, 2.5, 5.0)]
    cands += [2.0 ** -j for j in range(0, 24)]
    cands = np.array(cands, dtype=np.float64)
    nearest = float(cands[np.argmin(np.abs(np.log(cands) - np.log(a)))])
    rel_err = abs(a - nearest) / nearest
    return {"nice": bool(rel_err <= tol), "nearest": nearest, "rel_err": round(float(rel_err), 4)}


def linear_fit_r2(raw: np.ndarray, ref: np.ndarray) -> tuple[float, float, float]:
    """Linearer Fit ref ~ offset + scale*raw -> (scale, offset, r2).

    Im Unterschied zu Spearman (monoton/skalenfrei) misst R², wie gut das Feld die
    Referenz LINEAR rekonstruiert - ein grobes oder falsch skaliertes Feld, das nur
    MIT dem Signal mitlaeuft, faellt hier messbar ab.
    """
    raw = np.asarray(raw, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    valid = np.isfinite(raw) & np.isfinite(ref)
    if valid.sum() < 5 or np.ptp(raw[valid]) == 0:
        return 0.0, 0.0, 0.0
    scale, offset = np.polyfit(raw[valid], ref[valid], 1)
    fitted = offset + scale * raw[valid]
    ss_res = float(np.sum((ref[valid] - fitted) ** 2))
    ss_tot = float(np.sum((ref[valid] - ref[valid].mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(scale), float(offset), float(r2)


def spearman_r(a: np.ndarray, b: np.ndarray) -> float:
    """Kleiner Convenience-Wrapper: Spearman-Rangkorrelation, NaN-sicher, 0.0 bei zu wenig Daten."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    valid = np.isfinite(a) & np.isfinite(b)
    if valid.sum() < 5 or a[valid].std() < 1e-12 or b[valid].std() < 1e-12:
        return 0.0
    try:
        r = spearmanr(a[valid], b[valid]).statistic
        return float(r) if np.isfinite(r) else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Agnostische Sentinel-/Ausreisser-Erkennung
# ---------------------------------------------------------------------------

def _to_unsigned(raw: np.ndarray, length: int) -> np.ndarray:
    return np.mod(np.asarray(raw, dtype=np.float64), float(1 << int(length)))


def _is_all_ones(v: float) -> bool:
    iv = int(round(v))
    return iv > 0 and (iv & (iv + 1)) == 0


def _robust_band(x: np.ndarray) -> tuple[float, float, float]:
    med = float(np.median(x))
    mad = 1.4826 * float(np.median(np.abs(x - med)))
    if mad <= 0.0:
        q1, q3 = (float(v) for v in np.percentile(x, [25, 75]))
        mad = (q3 - q1) / 1.349
    return med, mad, max(mad, 1.0)


def _structural_anchors(length: int) -> list[float]:
    L = int(length)
    anchors = {0.0, float((1 << L) - 1), float(1 << (L - 1))}
    anchors.update(float((1 << k) - 1) for k in range(1, L + 1))
    return sorted(anchors)


def detect_extreme_outliers(raw: np.ndarray, length: int, *, gap_mad: float = 4.0,
                            k_band: float = 6.0, teleport_p: float = 0.999,
                            teleport_factor: float = 3.0, sanity_frac: float = 0.45,
                            min_n: int = 20, t: np.ndarray | None = None) -> dict | None:
    """Markiert Sentinel-/'Signal ungueltig'-Frames AGNOSTISCH (kein Wert/Vorzeichen/
    Haeufigkeits-Bias). Ein Sentinel liest weit ausserhalb der echten Datenbande (z.B.
    ein 16-Bit-Wert bei 0xFFFF, oder auch nicht-Allzahl-Codes wie 0x3FFF/0x4000/0x4041).

    Vier skalenfreie Saeulen: (1) robuste Median+/-MAD-Bande, (2) geforderte LEERE Luecke
    zum Bulk pro Seite (die primaere False-Positive-Bremse - ein lueckenloses Vollbereichs-
    signal wird auf keiner Seite geflaggt), (3) Teleport-Bestaetigung (Sentinel springt in
    einem Frame, ein echtes Signal rampt), (4) Episoden zaehlen statt Rohframes (eine lange
    Motor-aus-Phase ist wenige Episoden, kein Prozentsatz-Deckel).

    raw : dekodierte Rohwerte in Frame-Reihenfolge (Vorzeichen bereits angewandt falls signed).
    length : Feldlaenge in Bits (definiert die strukturellen Sentinel-Kandidaten).
    t : optionale Zeitstempel pro Frame (fuer echte Slew-Rate statt Pro-Frame-Delta).

    Rueckgabe None (nichts Auffaelliges) oder ein dict mit u.a. mask, count, frac,
    u_values, kind ('sentinel'/'outlier'/'suspect'), confidence ('high'/'medium'/'low').
    """
    raw = np.asarray(raw, dtype=np.float64)
    finite = np.isfinite(raw)
    n = int(finite.sum())
    if n < min_n:
        return None
    x = raw[finite]
    med, mad, scale = _robust_band(x)
    z = (raw - med) / scale
    cand = finite & (np.abs(z) > k_band)
    if int(cand.sum()) == 0:
        return None
    if cand.sum() / n > sanity_frac:
        return None

    def _side_gap(side_mask, hi):
        if not side_mask.any():
            return 0.0
        zc = z[side_mask]
        edge = float(zc.min()) if hi else float(zc.max())
        below = z[finite & ((z < edge) if hi else (z > edge))]
        if below.size == 0:
            return 0.0
        nearest = float(below.max()) if hi else float(below.min())
        return abs(edge - nearest)

    mask_hi = cand & (z > 0)
    mask_lo = cand & (z < 0)
    gap_hi, gap_lo = _side_gap(mask_hi, True), _side_gap(mask_lo, False)
    hi_ok = mask_hi.any() and gap_hi >= gap_mad
    lo_ok = mask_lo.any() and gap_lo >= gap_mad
    mask = np.zeros_like(finite)
    if hi_ok:
        mask |= mask_hi
    if lo_ok:
        mask |= mask_lo
    cnt = int(mask.sum())
    if cnt == 0:
        return None
    cluster_spread = float(np.ptp(z[mask])) if cnt > 1 else 0.0

    use_rate = t is not None and np.all(np.diff(np.asarray(t, float)) > 0)
    d = np.abs(np.diff(raw))
    if use_rate:
        d = d / np.diff(np.asarray(t, dtype=np.float64))
    in_band = finite & ~mask
    pair_ok = in_band[:-1] & in_band[1:]
    d_in = d[pair_ok & np.isfinite(d)]
    if d_in.size >= 10:
        budget = float(np.percentile(d_in, 100.0 * teleport_p))
    elif d_in.size:
        budget = float(d_in.max())
    else:
        budget = scale
    budget = max(budget, 1e-9)

    episodes = confirmed = 0
    i = 0
    while i < len(mask):
        if not mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(mask) and mask[j + 1]:
            j += 1
        episodes += 1
        edges = []
        if i - 1 >= 0 and in_band[i - 1] and np.isfinite(d[i - 1]):
            edges.append(d[i - 1])
        if j + 1 < len(mask) and in_band[j + 1] and np.isfinite(d[j]):
            edges.append(d[j])
        if edges and max(edges) > teleport_factor * budget:
            confirmed += 1
        i = j + 1

    u = _to_unsigned(raw[mask], length)
    uvals = sorted({float(v) for v in u})
    anchors = _structural_anchors(length)
    near_structural = any(min(abs(v - a) for a in anchors) <= 2.0 for v in uvals)
    looks_maxed = all(_is_all_ones(v) or v == float((1 << int(length)) - 1) for v in uvals)

    if episodes and confirmed == episodes:
        confidence = "high"
    elif confirmed >= 1:
        confidence = "medium"
    else:
        confidence = "low"
    order = ["low", "medium", "high"]
    if cluster_spread > gap_mad:
        confidence = order[max(0, order.index(confidence) - 1)]
    if n < 2 * min_n:
        confidence = order[min(order.index(confidence), 1)]
    kind = ("sentinel" if near_structural else "outlier") if confirmed >= 1 else "suspect"

    return {"mask": mask, "count": cnt, "frac": cnt / n, "u_values": uvals,
            "looks_maxed": bool(looks_maxed), "near_structural": bool(near_structural),
            "gap_mad_hi": gap_hi if hi_ok else 0.0, "gap_mad_lo": gap_lo if lo_ok else 0.0,
            "episodes": episodes, "confirmed_episodes": confirmed,
            "kind": kind, "confidence": confidence}


def auto_mask_outliers(raw: np.ndarray, length: int, t: np.ndarray | None = None, *,
                       require: str = "high", max_mask_frac: float = 0.15) -> tuple[np.ndarray, dict | None]:
    """Maskiert Sentinels VOR dem Scoring (setzt sie auf NaN), aber nur wenn die Erkennung
    mindestens `require`-Konfidenz hat UND hoechstens `max_mask_frac` der Frames betrifft -
    genau diese Schranke verhindert, dass ein FALSCHES Feld (diffuse Streuung, keine klare
    Luecke/Teleport) durch Maskierung 'gerettet' wird. Downstream-Scorer (linear_fit_r2,
    plausibility, spearman_r) filtern isfinite, maskierte Frames fallen also einfach raus."""
    info = detect_extreme_outliers(raw, length, t=t)
    if not info:
        return raw, None
    order = {"low": 0, "medium": 1, "high": 2}
    if order.get(info["confidence"], 0) < order.get(require, 2):
        return raw, None
    if info["frac"] > max_mask_frac:
        return raw, None
    out = np.array(raw, dtype=np.float64)
    out[info["mask"]] = np.nan
    return out, info


def describe_extreme_outliers(info: dict, length: int, scale: float | None = None,
                              offset: float = 0.0, unit: str = "") -> str:
    """Einzeiliger Klartext-Report zu einem detect_extreme_outliers()-Ergebnis."""
    u = info["u_values"]
    hexs = ", ".join(f"0x{int(v):0{(length + 3) // 4}X}" for v in u)
    phys = ""
    if scale is not None and u:
        decoded = [offset + scale * v for v in u]
        phys = " -> dekodiert " + ", ".join(f"{d:.4g}{unit}" for d in decoded)
    ep = f" in {info['episodes']} Episode(n)" if info.get("episodes") else ""
    gap = max(info.get("gap_mad_hi", 0.0), info.get("gap_mad_lo", 0.0))
    return (f"{info['count']} Frame(s){ep} ({100 * info['frac']:.2f}%) bei Rohwert {hexs} "
            f"[{info['kind']}, Konfidenz {info['confidence']}]{phys}; Luecke zum Bulk: "
            f"{gap:.0f}*MAD.")


# ---------------------------------------------------------------------------
# Bias-gated Scale/Offset-Nachkorrektur
# ---------------------------------------------------------------------------

ROUND_SCALE_TOL = 0.03
ROUND_BIAS_BUDGET = 0.01
ANCHOR_BIAS_BUDGET = 0.06
ANCHOR_ZERO_TOL = 0.05
ANCHOR_NEGLIGIBLE = 0.005


def _snap_offset(off: float, rng: float) -> float:
    if abs(off) <= 0.02 * rng:
        return 0.0
    if abs(off - round(off)) <= 0.02 * rng:
        return float(round(off))
    return float(off)


def propose_round_calibration(scale: float, offset: float, raw: np.ndarray, ref: np.ndarray,
                              scale_tol: float = ROUND_SCALE_TOL,
                              bias_budget: float = ROUND_BIAS_BUDGET) -> dict | None:
    """Schlaegt eine 'huebsche' OEM-Scale/Offset vor - gated auf systematischen Bias,
    NICHT auf R². R²/Spearman sind fast blind fuer eine kleine multiplikative Scale-
    Abweichung (2-3% Steigungsaenderung aendert R² kaum) - genau deshalb reicht ein
    R²-Gate nicht: es wuerde z.B. 0,0983 -> 0,1 runden, obwohl das durchgehend ~3% zu
    hoch liest (echte Scale ist 0,098, nicht 0,1 - z.B. Tacho- vs. echte Geschwindigkeit).

    `auto=True`  -> Bias-Budget eingehalten, sicher automatisch anwendbar.
    `auto=False` -> wuerde sichtbaren Bias einfuehren, nur als Vorschlag melden.
    """
    raw = np.asarray(raw, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    v = np.isfinite(raw) & np.isfinite(ref)
    raw, ref = raw[v], ref[v]
    if len(raw) < 5 or np.ptp(raw) == 0:
        return None
    rng = abs(scale) * float(np.ptp(raw))
    if rng == 0:
        return None
    sp = scale_plausibility(scale, tol=scale_tol)
    nice_scale = (-1.0 if scale < 0 else 1.0) * sp["nearest"] if sp["nice"] else float(scale)
    scale_changed = nice_scale != scale
    if scale_changed:
        nice_offset = _snap_offset(float(np.median(ref - nice_scale * raw)), rng)
    else:
        nice_offset = _snap_offset(float(offset), rng)
    offset_changed = nice_offset != offset
    if not (scale_changed or offset_changed):
        return None
    diff = (nice_scale - scale) * raw + (nice_offset - offset)
    max_bias = float(np.max(np.abs(diff)))
    bias_frac = max_bias / rng
    return {"scale": nice_scale, "offset": nice_offset, "scale_changed": scale_changed,
            "offset_changed": offset_changed, "max_bias": max_bias, "bias_frac": bias_frac,
            "auto": bias_frac <= bias_budget}


def detect_rest_cluster(ref: np.ndarray, frac_band: float = 0.03, min_n: int = 10,
                        min_frac: float = 0.02) -> tuple | None:
    """Findet einen dichten Ruhe-Cluster am UNTEREN Ende einer Referenzreihe (geparkt/
    Leerlauf). Rueckgabe (mask, ref_level, n) oder None, wenn kein echter Ruhezustand
    (nur ein kurzer Durchgangswert) erkennbar ist."""
    ref = np.asarray(ref, dtype=np.float64)
    v = np.isfinite(ref)
    if int(v.sum()) < min_n:
        return None
    r = ref[v]
    rng = float(np.ptp(r))
    if rng == 0:
        return None
    rmin = float(np.min(r))
    in_band = r <= rmin + frac_band * rng
    n = int(in_band.sum())
    if n < max(min_n, int(min_frac * len(r))):
        return None
    ref_level = float(np.median(r[in_band]))
    full = np.zeros(len(ref), dtype=bool)
    full[np.flatnonzero(v)[in_band]] = True
    return full, ref_level, n


def propose_anchor_calibration(scale: float, offset: float, raw: np.ndarray, ref: np.ndarray,
                               anchor_value: float | None = None,
                               bias_budget: float = ANCHOR_BIAS_BUDGET,
                               zero_tol: float = ANCHOR_ZERO_TOL) -> dict | None:
    """Pinnt die Kalibriergerade so um, dass ein bekannter Ruhezustand (meist Stillstand/
    Leerlauf = 0) exakt trifft - R²/Spearman sind blind fuer einen konstanten Offset, ein
    freier Fit kann also z.B. -1,6 km/h im Stand liefern, obwohl das physikalisch unmoeglich
    ist. Findet den Ruhe-Cluster (detect_rest_cluster) + den MODALEN Rohwert dort, und
    fitted die Steigung neu DURCH diesen Ankerpunkt (nicht nur den Offset verschieben -
    das wuerde die fuer den alten Offset gefittete Steigung behalten und in Bewegung
    systematisch daneben liegen).

    `anchor_value=None` -> nur automatisch erkannt, wenn der Ruhe-Cluster nahe 0 liegt
    (universeller Fall: Geschwindigkeit/Durchfluss/Strom/Drehmoment = 0 im Stand). Ein
    von-Null-verschiedener Ruhezustand (z.B. Leerlaufdrehzahl ~800) braucht explizit
    `anchor_value=800.0`.

    `auto=True` -> die noetige Verschiebung ist klein (Rauschpegel-Bereinigung).
    `auto=False` -> grosse Abweichung, Feldgeometrie vermutlich falsch - nicht automatisch
    anwenden, sondern melden.
    """
    raw = np.asarray(raw, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    v = np.isfinite(raw) & np.isfinite(ref)
    if int(v.sum()) < 5:
        return None
    rng = abs(float(scale)) * float(np.ptp(raw[v]))
    if rng == 0:
        return None
    rc = detect_rest_cluster(ref)
    if rc is None:
        return None
    rest_mask, ref_level, n_rest = rc
    m = rest_mask & v
    if int(m.sum()) < 5:
        return None
    vals, counts = np.unique(np.rint(raw[m]).astype(np.int64), return_counts=True)
    raw_rest = float(vals[int(np.argmax(counts))])
    if anchor_value is None:
        if abs(ref_level) > zero_tol * rng:
            return None
        target = 0.0
    else:
        target = float(anchor_value)
    current = float(scale) * raw_rest + float(offset)
    delta = target - current
    if abs(delta) <= ANCHOR_NEGLIGIBLE * rng:
        return None
    bias_frac = abs(delta) / rng
    x, y = raw[v], ref[v]
    dx = x - raw_rest
    denom = float(np.sum(dx * dx))
    if denom <= 0:
        return None
    new_scale = float(np.sum(dx * (y - target)) / denom)
    new_offset = target - new_scale * raw_rest
    return {"scale": new_scale, "offset": new_offset, "old_scale": float(scale),
            "delta": delta, "anchor_value": target, "ref_level": ref_level,
            "raw_rest": raw_rest, "current": current, "n_rest": n_rest,
            "bias_frac": bias_frac, "auto": bias_frac <= bias_budget}


def demo():
    """Selbsttest (Ponytail-Vorgabe: nicht-trivialer Code braucht einen Check).
    Faelle adaptiert aus common.py --selftest, auf die hier portierte Teilmenge reduziert."""
    ok = True

    # plausibility: ein voller 16-Bit-Ramp muss sein umlaufendes unteres Byte outscoren
    ramp16 = np.linspace(1000, 60000, 500)
    low8 = ramp16.astype(np.int64) & 0xFF
    p_full, p_slice = plausibility(ramp16, 16)["score"], plausibility(low8, 8)["score"]
    check1 = p_full > p_slice and p_full > 0.5
    print(f"  plausibility ramp16={p_full:.3f} > lowbyte8={p_slice:.3f} -> {'OK' if check1 else 'FAIL'}")
    ok &= check1

    # scale_plausibility: huebsche vs. haessliche Scales
    nice = [1e-6, 0.001, 0.125, 0.5, 1.0, 0.25, 2.5]
    bad = [-1 / 2593.0, 1 / 777.0]
    check2 = all(scale_plausibility(s)["nice"] for s in nice) and \
        all(not scale_plausibility(s)["nice"] for s in bad)
    print(f"  scale_plausibility nice/bad -> {'OK' if check2 else 'FAIL'}")
    ok &= check2

    # detect_extreme_outliers: agnostisch (Wert/Vorzeichen/Haeufigkeit-unabhaengig)
    bulk = np.linspace(0, 700, 800)
    sat = np.concatenate([bulk, [4095.0, 4095.0]])
    legacy = detect_extreme_outliers(sat, 16)
    clean = detect_extreme_outliers(bulk, 16)
    fullrange = detect_extreme_outliers(np.linspace(0, 65535, 2000), 16)
    rpm = np.linspace(600, 2300, 800)
    trip = np.tile([16383.0, 16384.0, 16449.0], 15)  # nicht-Allzahl-Sentinel-Trio
    multi = detect_extreme_outliers(np.concatenate([rpm, trip]), 16)
    check3 = (legacy is not None and legacy["kind"] == "sentinel" and legacy["confidence"] == "high"
              and clean is None and fullrange is None
              and multi is not None and multi["near_structural"] and multi["confidence"] == "high")
    print(f"  detect_extreme_outliers (sentinel/clean/fullrange/multi-value) -> {'OK' if check3 else 'FAIL'}")
    ok &= check3

    # propose_round_calibration: sauber runden vs. Bias flaggen
    rawv = np.arange(0, 700, 2, dtype=np.float64)
    refA = np.round(0.1 * rawv)
    sA, oA = np.polyfit(rawv, refA, 1)
    prA = propose_round_calibration(sA, oA, rawv, refA)
    refB = 0.0976 * rawv  # 2.4% daneben -> Snap auf 0.1 waere Bias, nicht Rauschen
    sB, oB = np.polyfit(rawv, refB, 1)
    prB = propose_round_calibration(sB, oB, rawv, refB)
    check4 = (prA is not None and abs(prA["scale"] - 0.1) < 1e-9 and prA["auto"]
              and prB is not None and not prB["auto"])
    print(f"  propose_round_calibration (clean-auto / biased-flag) -> {'OK' if check4 else 'FAIL'}")
    ok &= check4

    # propose_anchor_calibration: Nullpunkt-Refit
    rawS = np.concatenate([np.zeros(120), np.arange(1, 700, 3, dtype=np.float64)])
    refS = 0.1 * rawS
    acA = propose_anchor_calibration(0.0983, -1.57, rawS, refS)  # Fit driftete negativ
    acB = propose_anchor_calibration(0.1, 0.0, rawS, refS)       # schon korrekt -> nichts zu tun
    check5 = (acA is not None and acA["auto"] and abs(acA["scale"] - 0.1) < 1e-6
              and abs(acA["offset"]) < 1e-6 and acB is None)
    print(f"  propose_anchor_calibration (drift-refit / already-ok) -> {'OK' if check5 else 'FAIL'}")
    ok &= check5

    print("ALLES OK" if ok else "MINDESTENS EIN CHECK FEHLGESCHLAGEN")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if demo() else 1)
