"""Runde aus OSM-Wegen: Einstieg an inneren Knoten, geschlossene Kette, fehlender Anschluss ist ein Fehler."""
import numpy as np

import oschersleben_track_model as T


def test_chain_ways_uses_inner_nodes_and_closes():
    # Weg 1 hat einen Stummel vor dem Anschluss (Knoten 9) und endet in Knoten 3, wo Weg 2 beginnt
    ways = {1: [9, 0, 1, 2, 3], 2: [3, 4, 5], 3: [5, 6, 0]}
    assert T.chain_ways(ways, np.array([1, 2, 3])) == [0, 1, 2, 3, 4, 5, 6]


def test_chain_ways_rejects_gap():
    ways = {1: [0, 1], 2: [2, 3], 3: [3, 0]}
    try:
        T.chain_ways(ways, np.array([1, 2, 3]))
    except ValueError:
        return
    raise AssertionError("Luecke wurde nicht erkannt")


def test_fit_line_ignores_outliers():
    s = np.arange(100, dtype=float)
    y = 2.0 + 0.01 * s
    y[40:50] += 3.0   # Fahrzeug oder Stuetze im Profil
    coef, share = T.fit_line(s, y)
    assert abs(coef[0] - 0.01) < 1e-6 and abs(coef[1] - 2.0) < 1e-6
    assert abs(share - 0.9) < 1e-9


if __name__ == "__main__":
    test_fit_line_ignores_outliers()
    test_chain_ways_uses_inner_nodes_and_closes()
    test_chain_ways_rejects_gap()
    print("ok")
