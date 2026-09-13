use crate::model::Symbol;
use std::collections::HashMap;

/// Convergence tolerance (L1 residual) and iteration cap.
pub const CONVERGENCE_TOL: f64 = 1e-6;
pub const MAX_ITERATIONS: usize = 100;

/// Weighted PageRank result: iterations run and whether the L1 residual
/// dropped below [`CONVERGENCE_TOL`] before the cap.
pub struct PagerankStats {
    pub iters: usize,
    pub converged: bool,
}

pub fn compute_in_degree_pagerank(
    symbols: &mut [Symbol],
    callers_map: &HashMap<usize, Vec<usize>>,
    callees_map: &HashMap<usize, Vec<usize>>,
    focus_indices: Option<&[usize]>,
) -> PagerankStats {
    compute_weighted_pagerank(symbols, callers_map, callees_map, &HashMap::new(), focus_indices)
}

/// Weighted variant: `edge_weights[(u, v)]` is the sublinear multiplicity
/// weight of the u→v edge; missing entries weigh 1.0 (unweighted legacy).
pub fn compute_weighted_pagerank(
    symbols: &mut [Symbol],
    callers_map: &HashMap<usize, Vec<usize>>,
    callees_map: &HashMap<usize, Vec<usize>>,
    edge_weights: &HashMap<(usize, usize), f64>,
    focus_indices: Option<&[usize]>,
) -> PagerankStats {
    let n = symbols.len();
    if n == 0 {
        return PagerankStats {
            iters: 0,
            converged: true,
        };
    }

    let damping = 0.85;

    let mut scores = vec![1.0 / n as f64; n];
    let mut next_scores = vec![0.0; n];

    // Personalization vector
    let personalization: Vec<f64> = match focus_indices {
        Some(indices) if !indices.is_empty() => {
            let mut p = vec![0.0; n];
            let mass = 1.0 / indices.len() as f64;
            for &idx in indices {
                if idx < n {
                    p[idx] = mass;
                }
            }
            p
        }
        _ => vec![1.0 / n as f64; n],
    };

    let edge_w = |u: usize, v: usize| -> f64 {
        edge_weights.get(&(u, v)).copied().unwrap_or(1.0)
    };

    // Weighted out-mass per node; unweighted out-degree when no weights.
    let out_weight: Vec<f64> = (0..n)
        .map(|u| match callees_map.get(&u) {
            Some(callees) if !edge_weights.is_empty() => {
                callees.iter().map(|&v| edge_w(u, v)).sum()
            }
            Some(callees) => callees.len() as f64,
            None => 0.0,
        })
        .collect();

    let callers_of: Vec<&[usize]> = (0..n)
        .map(|v| callers_map.get(&v).map_or(&[][..], |c| c.as_slice()))
        .collect();

    // Which nodes are dangling never changes; only their scores do.
    let dangling: Vec<usize> = (0..n).filter(|&u| out_weight[u] == 0.0).collect();

    let mut stats = PagerankStats {
        iters: 0,
        converged: false,
    };
    for iter in 0..MAX_ITERATIONS {
        let dangling_sum: f64 = dangling.iter().map(|&u| scores[u]).sum();

        for v in 0..n {
            let mut in_sum = 0.0;
            for &u in callers_of[v] {
                let total = out_weight[u];
                if total > 0.0 {
                    in_sum += scores[u] * edge_w(u, v) / total;
                }
            }

            next_scores[v] = (1.0 - damping) * personalization[v]
                + damping * (in_sum + dangling_sum * personalization[v]);
        }

        let residual: f64 = next_scores
            .iter()
            .zip(scores.iter())
            .map(|(a, b)| (a - b).abs())
            .sum();
        scores.copy_from_slice(&next_scores);
        stats.iters = iter + 1;
        if residual < CONVERGENCE_TOL {
            stats.converged = true;
            break;
        }
    }

    // Normalize and assign to symbols
    for (i, sym) in symbols.iter_mut().enumerate() {
        sym.centrality = scores[i];
    }
    stats
}
