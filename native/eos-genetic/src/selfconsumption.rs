//! Self-consumption table: `prediction/interpolator.py` (`SelfConsumptionProbabilityInterpolator`,
//! `calculate_expected_direct_consumption`) with `utils/gridinterpolator.py`
//! (`LinearGridInterpolator`, bilinear like SciPy `RegularGridInterpolator(method="linear")`).
//!
//! The table (`data/regular_grid_interpolator.pkl` from EOS, SHA-256 3323bc0e…) is bundled as
//! `data/self_consumption_table.bin`: two u32 (axis lengths), then the axes and the
//! values as f64 (little endian, rows = mean load).

use crate::numpy::{DotKernel, FxHashMap, pairwise_sum, py_max, py_min, searchsorted_right};

static TABLE: &[u8] = include_bytes!("../data/self_consumption_table.bin");

/// SHA-256 of the EOS file the bundled table was converted from
/// (`akkudoktoreos/data/regular_grid_interpolator.pkl`). The Python side refuses the kernel if
/// its own table differs.
pub const SOURCE_SHA256: &str = "3323bc0e59660699512ddcd1d53e4c1634cc7a8cf9795378fc04765d85bc8dba";

/// The table, read once.
#[derive(Debug, Clone)]
pub struct Table {
    pub axis0: Vec<f64>,
    pub axis1: Vec<f64>,
    pub values: Vec<f64>,
    pub fill_value: f64,
}

impl Table {
    pub fn bundled() -> Table {
        let rd = |off: usize| u32::from_le_bytes([TABLE[off], TABLE[off + 1], TABLE[off + 2], TABLE[off + 3]]) as usize;
        let n0 = rd(0);
        let n1 = rd(4);
        let f = |i: usize| {
            let o = 8 + 8 * i;
            let mut b = [0u8; 8];
            b.copy_from_slice(&TABLE[o..o + 8]);
            f64::from_le_bytes(b)
        };
        let axis0 = (0..n0).map(f).collect();
        let axis1 = (n0..n0 + n1).map(f).collect();
        let values = (n0 + n1..n0 + n1 + n0 * n1).map(f).collect();
        // fill_value of the bundled table is 0.
        Table { axis0, axis1, values, fill_value: 0.0 }
    }
}

/// Computes `calculate_expected_direct_consumption`, with the same (purely accelerating)
/// caches as Python.
#[derive(Debug, Clone)]
pub struct SelfConsumption {
    t: Table,
    dot: DotKernel,
    load_min: f64,
    load_max: f64,
    distributions: FxHashMap<u64, Vec<f64>>,
    direct: FxHashMap<(u64, u64), f64>,
    /// Calls without a cache hit (diagnostics).
    pub misses: u64,
}

impl SelfConsumption {
    pub fn new(dot: DotKernel) -> SelfConsumption {
        let t = Table::bundled();
        let load_min = t.axis0[0];
        let load_max = t.axis0[t.axis0.len() - 1];
        SelfConsumption {
            t,
            dot,
            load_min,
            load_max,
            distributions: FxHashMap::with_capacity_and_hasher(512, Default::default()),
            direct: FxHashMap::with_capacity_and_hasher(8192, Default::default()),
            misses: 0,
        }
    }

    /// `LinearGridInterpolator.__call__` for the points (mean load, minute load k).
    fn interpolate_row(&self, mean_load: f64, out: &mut Vec<f64>) {
        let a0 = &self.t.axis0;
        let a1 = &self.t.axis1;
        let n1 = a1.len();
        out.clear();
        let nan0 = mean_load.is_nan();
        let (i0, y0, oob0) = {
            let idx = searchsorted_right(a0, mean_load) as isize - 1;
            let idx = idx.clamp(0, a0.len() as isize - 2) as usize;
            let y = (mean_load - a0[idx]) / (a0[idx + 1] - a0[idx]);
            (idx, y, mean_load < a0[0] || mean_load > a0[a0.len() - 1])
        };
        for k in 0..n1 {
            let x1 = a1[k];
            let idx = searchsorted_right(a1, x1) as isize - 1;
            let i1 = idx.clamp(0, n1 as isize - 2) as usize;
            let y1 = (x1 - a1[i1]) / (a1[i1 + 1] - a1[i1]);
            let oob = oob0 || x1 < a1[0] || x1 > a1[n1 - 1];
            let v = |r: usize, c: usize| self.t.values[r * n1 + c];
            let mut result = v(i0, i1) * (1.0 - y0) * (1.0 - y1);
            result += v(i0, i1 + 1) * (1.0 - y0) * y1;
            result += v(i0 + 1, i1) * y0 * (1.0 - y1);
            result += v(i0 + 1, i1 + 1) * y0 * y1;
            if oob {
                result = self.t.fill_value;
            }
            if nan0 || x1.is_nan() {
                result = f64::NAN;
            }
            out.push(result);
        }
    }

    /// `_load_distribution(mean_load_power_w)` → probabilities (normalized).
    fn load_distribution(&mut self, mean_load_power_w: f64) -> Vec<f64> {
        let bounded = py_min(py_max(mean_load_power_w, self.load_min), self.load_max);
        if let Some(d) = self.distributions.get(&bounded.to_bits()) {
            return d.clone();
        }
        let mut probs = Vec::with_capacity(self.t.axis1.len());
        self.interpolate_row(bounded, &mut probs);
        // np.maximum(p, 0.0): NaN stays NaN.
        for p in probs.iter_mut() {
            if !(p.is_nan() || *p >= 0.0) {
                *p = 0.0;
            }
        }
        let sum = 0.0 + pairwise_sum(&probs);
        if !(sum <= 0.0) {
            for p in probs.iter_mut() {
                *p /= sum;
            }
        }
        if !bounded.is_nan() {
            if self.distributions.len() >= 4096 {
                self.distributions.clear();
            }
            self.distributions.insert(bounded.to_bits(), probs.clone());
        }
        probs
    }

    /// `calculate_expected_direct_consumption(mean_load_power_w, pv_power_w)` [W].
    pub fn expected_direct_consumption(&mut self, mean_load_power_w: f64, pv_power_w: f64) -> f64 {
        let key = (mean_load_power_w.to_bits(), pv_power_w.to_bits());
        if let Some(&v) = self.direct.get(&key) {
            return v;
        }
        self.misses += 1;
        let v = self.expected_uncached(mean_load_power_w, pv_power_w);
        if self.direct.len() >= 8192 {
            self.direct.clear();
        }
        self.direct.insert(key, v);
        v
    }

    fn expected_uncached(&mut self, mean_load_power_w: f64, pv_power_w: f64) -> f64 {
        let mean = py_max(mean_load_power_w, 0.0);
        let pv = py_max(pv_power_w, 0.0);
        if mean == 0.0 || pv == 0.0 {
            return 0.0;
        }
        let probs = self.load_distribution(mean);
        let levels = &self.t.axis1;
        let modeled = self.dot.dot(&probs, levels);
        if modeled <= 0.0 {
            return 0.0;
        }
        let ratio = mean / modeled;
        let clipped: Vec<f64> = levels
            .iter()
            .map(|&l| {
                let x = l * ratio;
                // np.minimum(x, pv): NaN wins.
                if x.is_nan() || pv.is_nan() {
                    f64::NAN
                } else if x <= pv {
                    x
                } else {
                    pv
                }
            })
            .collect();
        let expected = self.dot.dot(&probs, &clipped);
        py_min(py_max(expected, 0.0), py_min(mean, pv))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_loads() {
        let t = Table::bundled();
        assert_eq!(t.axis0.len(), 70);
        assert_eq!(t.axis1.len(), 100);
        assert_eq!(t.values.len(), 7000);
        assert_eq!(t.axis0[1], 50.0);
    }

    #[test]
    fn direct_consumption_is_bounded() {
        let mut sc = SelfConsumption::new(DotKernel::Sequential);
        let v = sc.expected_direct_consumption(500.0, 300.0);
        assert!(v > 0.0 && v <= 300.0);
        assert_eq!(sc.expected_direct_consumption(0.0, 300.0), 0.0);
    }
}
