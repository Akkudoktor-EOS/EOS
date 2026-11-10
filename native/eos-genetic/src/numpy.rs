//! NumPy computations, reproduced bit-identically (only those GENETIC uses).
//!
//! * `pairwise_sum` = `DOUBLE_pairwise_sum` (np.sum / np.add.reduce on float64, 1-D),
//! * `nansum`, `mean`,
//! * `interp` = `arr_interp` (np.interp), optionally with fused multiply-add,
//!   because NumPy builds for aarch64 contract the line `slope*(x - xp[j]) + fp[j]` to FMA,
//! * `quantile` (method "linear", `_lerp`),
//! * `dot`: in NumPy the dot product goes through BLAS (`cblas_ddot`); the summation order
//!   depends on the BLAS kernel, so Python EOS differs in the last bits from CPU to CPU.
//!   **Release:** one fixed profile (`NumericProfile::RELEASE`, sequential, no FMA) – same
//!   plans on every device. The measured OpenBLAS kernels (disassembly of scipy-openblas64
//!   from the EOS image) are only available as an opt-in for parity diagnostics against Python.

/// Which `ddot` kernel is reproduced.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DotKernel {
    /// Simple loop without FMA (NumPy without BLAS).
    Sequential,
    /// OpenBLAS `ddot_k_CORTEXA53` (Cortex-A53/A55): 2 lanes, separate multiplication,
    /// remainder with FMA.
    OpenBlasCortexA53,
    /// OpenBLAS `ddot_k_NEOVERSEN1` (Neoverse N1, Apple cores under Linux): 16 lanes with FMA
    /// in blocks of 32, remainder with FMA.
    OpenBlasNeoverseN1,
}

impl DotKernel {
    pub fn parse(name: &str) -> Option<DotKernel> {
        match name.trim().to_ascii_lowercase().as_str() {
            "sequential" | "plain" | "none" => Some(DotKernel::Sequential),
            "cortexa53" | "cortexa55" | "openblas-cortexa53" | "a53" | "a55" => Some(DotKernel::OpenBlasCortexA53),
            "neoversen1" | "openblas-neoversen1" | "n1" => Some(DotKernel::OpenBlasNeoverseN1),
            _ => None,
        }
    }

    pub fn name(&self) -> &'static str {
        match self {
            DotKernel::Sequential => "sequential",
            DotKernel::OpenBlasCortexA53 => "cortexa53",
            DotKernel::OpenBlasNeoverseN1 => "neoversen1",
        }
    }

    /// The release profile: **always** `Sequential`, identical on every device (same request +
    /// seed → same plan everywhere). Only callers that explicitly want to reproduce a Python
    /// reference platform (parity diagnostics) set `EOS_GENETIC_DOT_KERNEL`.
    pub fn detect() -> DotKernel {
        std::env::var("EOS_GENETIC_DOT_KERNEL").ok().and_then(|v| DotKernel::parse(&v)).unwrap_or(DotKernel::Sequential)
    }

    /// The kernel OpenBLAS (DYNAMIC_ARCH) would pick on this machine – only for
    /// parity diagnostics against Python EOS on exactly this device, never in the release path
    /// (on heterogeneous CPUs such as A76+A55 not even Python is deterministic).
    pub fn openblas_for_this_cpu() -> DotKernel {
        if cfg!(all(target_os = "linux", target_arch = "aarch64")) {
            if let Ok(text) = std::fs::read_to_string("/proc/cpuinfo") {
                let mut implementer = None;
                let mut part = None;
                for line in text.lines() {
                    let mut it = line.splitn(2, ':');
                    let key = it.next().unwrap_or("").trim();
                    let val = it.next().unwrap_or("").trim();
                    if key == "CPU implementer" && implementer.is_none() {
                        implementer = u32::from_str_radix(val.trim_start_matches("0x"), 16).ok();
                    }
                    if key == "CPU part" && part.is_none() {
                        part = u32::from_str_radix(val.trim_start_matches("0x"), 16).ok();
                    }
                }
                return match (implementer, part) {
                    (Some(0x41), Some(0xd03)) | (Some(0x41), Some(0xd05)) => DotKernel::OpenBlasCortexA53,
                    (Some(0x41), Some(0xd0c))
                    | (Some(0x41), Some(0xd0b))
                    | (Some(0x41), Some(0xd0d))
                    | (Some(0x41), Some(0xd41)) => DotKernel::OpenBlasNeoverseN1,
                    (Some(0x61), _) => DotKernel::OpenBlasNeoverseN1,
                    _ => DotKernel::Sequential,
                };
            }
        }
        DotKernel::Sequential
    }

    /// Dot product of two series of equal length.
    pub fn dot(&self, a: &[f64], b: &[f64]) -> f64 {
        let n = a.len().min(b.len());
        match self {
            DotKernel::Sequential => {
                let mut s = 0.0;
                for i in 0..n {
                    s += a[i] * b[i];
                }
                s
            }
            DotKernel::OpenBlasCortexA53 => {
                // ld1 {v2,v3} / fmul / fadd v0+=v2 / fadd v0+=v3, then faddp, remainder fmadd.
                let mut l0 = 0.0f64;
                let mut l1 = 0.0f64;
                let blocks = n / 4;
                let mut d0 = 0.0f64;
                if blocks > 0 {
                    for k in 0..blocks {
                        let i = 4 * k;
                        let p0 = a[i] * b[i];
                        let p1 = a[i + 1] * b[i + 1];
                        let p2 = a[i + 2] * b[i + 2];
                        let p3 = a[i + 3] * b[i + 3];
                        l0 += p0;
                        l1 += p1;
                        l0 += p2;
                        l1 += p3;
                    }
                    d0 = l0 + l1;
                }
                for i in 4 * blocks..n {
                    d0 = a[i].mul_add(b[i], d0);
                }
                d0
            }
            DotKernel::OpenBlasNeoverseN1 => {
                let blocks = n / 32;
                let mut d0 = 0.0f64;
                if blocks > 0 {
                    let mut lane = [0.0f64; 16];
                    for k in 0..blocks {
                        let base = 32 * k;
                        for half in 0..2 {
                            for (l, acc) in lane.iter_mut().enumerate() {
                                let i = base + 16 * half + l;
                                *acc = a[i].mul_add(b[i], *acc);
                            }
                        }
                    }
                    // v_k = (lane[2k], lane[2k+1]); v0+=v1, v2+=v3, v4+=v5, v6+=v7, v0+=v2, v4+=v6, v0+=v4.
                    let v = |k: usize| [lane[2 * k], lane[2 * k + 1]];
                    let add = |x: [f64; 2], y: [f64; 2]| [x[0] + y[0], x[1] + y[1]];
                    let v01 = add(v(0), v(1));
                    let v23 = add(v(2), v(3));
                    let v45 = add(v(4), v(5));
                    let v67 = add(v(6), v(7));
                    let v0 = add(v01, v23);
                    let v4 = add(v45, v67);
                    let s = add(v0, v4);
                    d0 = s[0] + s[1];
                }
                for i in 32 * blocks..n {
                    d0 = a[i].mul_add(b[i], d0);
                }
                d0
            }
        }
    }
}

/// Numerical profile of the Python reference being reproduced.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NumericProfile {
    pub dot: DotKernel,
    /// `np.interp` with FMA (NumPy builds for aarch64).
    pub interp_fma: bool,
}

impl NumericProfile {
    /// Fixed release profile (device independent): sequential dot product, `np.interp` without
    /// FMA. Different reference profiles only via `EOS_GENETIC_DOT_KERNEL` /
    /// `EOS_GENETIC_INTERP_FMA` (parity diagnostics).
    pub const RELEASE: NumericProfile = NumericProfile { dot: DotKernel::Sequential, interp_fma: false };

    pub fn detect() -> NumericProfile {
        let interp_fma =
            matches!(std::env::var("EOS_GENETIC_INTERP_FMA").ok().as_deref(), Some("1") | Some("true") | Some("on"));
        NumericProfile { dot: DotKernel::detect(), interp_fma }
    }
}

impl Default for NumericProfile {
    fn default() -> Self {
        NumericProfile::detect()
    }
}

/// `DOUBLE_pairwise_sum` (np.sum on float64, contiguous, 1-D).
pub fn pairwise_sum(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        // Does NumPy start with -0.0? No: res = 0. in DOUBLE_pairwise_sum, then += a[i].
        let mut res = 0.0;
        for &v in a {
            res += v;
        }
        res
    } else if n <= 128 {
        let mut r = [a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]];
        let mut i = 8;
        while i < n - (n % 8) {
            r[0] += a[i];
            r[1] += a[i + 1];
            r[2] += a[i + 2];
            r[3] += a[i + 3];
            r[4] += a[i + 4];
            r[5] += a[i + 5];
            r[6] += a[i + 6];
            r[7] += a[i + 7];
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += a[i];
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        pairwise_sum(&a[..n2]) + pairwise_sum(&a[n2..])
    }
}

/// `np.sum` of a float64 series: reduction start value 0.0 + pairwise sum.
#[inline]
pub fn sum(a: &[f64]) -> f64 {
    0.0 + pairwise_sum(a)
}

/// `np.nansum` (NaN → 0, then `np.sum`), without an intermediate buffer.
pub fn nansum(a: &[f64]) -> f64 {
    0.0 + pairwise_sum_nan0(a)
}

#[inline(always)]
fn z(v: f64) -> f64 {
    if v.is_nan() { 0.0 } else { v }
}

/// `pairwise_sum` over `a` with NaN → 0 (same order as on a cleaned copy).
fn pairwise_sum_nan0(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = 0.0;
        for &v in a {
            res += z(v);
        }
        res
    } else if n <= 128 {
        let mut r = [z(a[0]), z(a[1]), z(a[2]), z(a[3]), z(a[4]), z(a[5]), z(a[6]), z(a[7])];
        let mut i = 8;
        while i < n - (n % 8) {
            for k in 0..8 {
                r[k] += z(a[i + k]);
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += z(a[i]);
            i += 1;
        }
        res
    } else {
        let mut n2 = n / 2;
        n2 -= n2 % 8;
        pairwise_sum_nan0(&a[..n2]) + pairwise_sum_nan0(&a[n2..])
    }
}

/// Fast hasher for float bit patterns and short integer sequences (no DoS protection needed:
/// all keys are created within the run itself).
#[derive(Default, Clone, Copy)]
pub struct FxHasher {
    hash: u64,
}

impl std::hash::Hasher for FxHasher {
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        for chunk in bytes.chunks(8) {
            let mut buf = [0u8; 8];
            buf[..chunk.len()].copy_from_slice(chunk);
            self.write_u64(u64::from_le_bytes(buf));
        }
    }
    #[inline]
    fn write_u64(&mut self, i: u64) {
        self.hash = (self.hash.rotate_left(5) ^ i).wrapping_mul(0x51_7c_c1_b7_27_22_0a_95);
    }
    #[inline]
    fn write_i64(&mut self, i: i64) {
        self.write_u64(i as u64);
    }
    #[inline]
    fn write_usize(&mut self, i: usize) {
        self.write_u64(i as u64);
    }
    #[inline]
    fn finish(&self) -> u64 {
        self.hash
    }
}

pub type FxBuild = std::hash::BuildHasherDefault<FxHasher>;
pub type FxHashMap<K, V> = std::collections::HashMap<K, V, FxBuild>;
pub type FxHashSet<K> = std::collections::HashSet<K, FxBuild>;

/// `np.mean` of a float64 series.
pub fn mean(a: &[f64]) -> f64 {
    if a.is_empty() {
        return f64::NAN;
    }
    sum(a) / a.len() as f64
}

/// `np.min` (NaN wins as in NumPy).
pub fn min(a: &[f64]) -> f64 {
    let mut m = a[0];
    for &v in &a[1..] {
        if v.is_nan() || m.is_nan() {
            return f64::NAN;
        }
        if v < m {
            m = v;
        }
    }
    m
}

/// `np.max` (NaN wins as in NumPy).
pub fn max(a: &[f64]) -> f64 {
    let mut m = a[0];
    for &v in &a[1..] {
        if v.is_nan() || m.is_nan() {
            return f64::NAN;
        }
        if v > m {
            m = v;
        }
    }
    m
}

/// `np.ptp`.
pub fn ptp(a: &[f64]) -> f64 {
    max(a) - min(a)
}

/// `np.interp(x, xp, fp)` for a single value (NumPy `arr_interp`).
pub fn interp(x: f64, xp: &[f64], fp: &[f64], fma: bool) -> f64 {
    let n = xp.len();
    let left = fp[0];
    let right = fp[n - 1];
    if n == 1 {
        return if x < xp[0] {
            left
        } else if x > xp[0] {
            right
        } else {
            fp[0]
        };
    }
    if x.is_nan() {
        return x;
    }
    let j: isize = if x > xp[n - 1] {
        n as isize
    } else if x < xp[0] {
        -1
    } else {
        let (mut lo, mut hi) = (0usize, n);
        while lo < hi {
            let mid = lo + ((hi - lo) >> 1);
            if x >= xp[mid] {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        lo as isize - 1
    };
    if j == -1 {
        return left;
    }
    if j == n as isize {
        return right;
    }
    let j = j as usize;
    if j == n - 1 {
        return fp[j];
    }
    if xp[j] == x {
        return fp[j];
    }
    let slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
    let lin = |s: f64, dx: f64, y: f64| if fma { s.mul_add(dx, y) } else { s * dx + y };
    let mut res = lin(slope, x - xp[j], fp[j]);
    if res.is_nan() {
        res = lin(slope, x - xp[j + 1], fp[j + 1]);
        if res.is_nan() && fp[j] == fp[j + 1] {
            res = fp[j];
        }
    }
    res
}

/// `np.quantile(a, q)` with the default method "linear" for a 1-D series.
pub fn quantile(a: &[f64], q: f64) -> f64 {
    let n = a.len();
    if n == 0 {
        return f64::NAN;
    }
    if a.iter().any(|v| v.is_nan()) {
        return f64::NAN;
    }
    let mut s = a.to_vec();
    s.sort_by(|x, y| x.partial_cmp(y).unwrap_or(std::cmp::Ordering::Equal));
    let virtual_index = (n as f64 - 1.0) * q;
    let mut previous = virtual_index.floor();
    let mut next = previous + 1.0;
    let (prev_i, next_i);
    if virtual_index >= n as f64 - 1.0 {
        previous = -1.0;
        next = -1.0;
    }
    if virtual_index < 0.0 {
        previous = 0.0;
        next = 0.0;
    }
    let idx = |v: f64| -> usize { if v < 0.0 { n - 1 } else { v as usize } };
    prev_i = idx(previous);
    next_i = idx(next);
    let gamma = virtual_index - previous;
    let a0 = s[prev_i];
    let b0 = s[next_i];
    let diff = b0 - a0;
    if gamma >= 0.5 { b0 - diff * (1.0 - gamma) } else { a0 + diff * gamma }
}

/// Python's `min(a, b)`: `b` only if `b < a`.
#[inline(always)]
pub fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// Python's `max(a, b)`: `b` only if `b > a`.
#[inline(always)]
pub fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Python's `bisect.bisect_left`.
#[inline]
pub fn bisect_left(a: &[f64], x: f64) -> usize {
    let (mut lo, mut hi) = (0usize, a.len());
    while lo < hi {
        let mid = (lo + hi) / 2;
        if a[mid] < x {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    lo
}

/// `np.searchsorted(a, x, side="right")`.
#[inline]
pub fn searchsorted_right(a: &[f64], x: f64) -> usize {
    let (mut lo, mut hi) = (0usize, a.len());
    while lo < hi {
        let mid = (lo + hi) / 2;
        if x < a[mid] {
            hi = mid;
        } else {
            lo = mid + 1;
        }
    }
    lo
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quantile_matches_known_values() {
        let a = [3.0, 1.0, 2.0, 4.0];
        assert_eq!(quantile(&a, 0.5), 2.5);
        assert_eq!(quantile(&a, 0.0), 1.0);
        assert_eq!(quantile(&a, 1.0), 4.0);
    }

    #[test]
    fn dot_kernels_agree_on_exact_values() {
        let a: Vec<f64> = (0..100).map(|i| i as f64).collect();
        let b = vec![1.0; 100];
        for k in [DotKernel::Sequential, DotKernel::OpenBlasCortexA53, DotKernel::OpenBlasNeoverseN1] {
            assert_eq!(k.dot(&a, &b), 4950.0);
        }
    }
}

/// NumPy's `Tag::less` for float64: NaN sorts last.
#[inline(always)]
fn np_less(a: f64, b: f64) -> bool {
    a < b || (b.is_nan() && !a.is_nan())
}

/// `np.argsort(v)` with the default kind "quicksort" (NumPy `aquicksort_`: introsort with
/// median of three, insertion sort from 16 elements, heapsort on too deep recursion).
/// Not stable – the order of equal values is NumPy's.
pub fn argsort_quicksort(v: &[f64]) -> Vec<usize> {
    const SMALL_QUICKSORT: isize = 15;
    let num = v.len();
    let mut tosort: Vec<usize> = (0..num).collect();
    if num <= 1 {
        return tosort;
    }
    let mut cdepth: i64 = {
        let mut n = num;
        let mut d = 0i64;
        n >>= 1;
        while n > 0 {
            d += 1;
            n >>= 1;
        }
        d * 2
    };
    let mut pl: isize = 0;
    let mut pr: isize = num as isize - 1;
    let mut stack: Vec<(isize, isize)> = Vec::new();
    let mut depth: Vec<i64> = Vec::new();
    let a = &mut tosort;
    loop {
        if cdepth < 0 {
            aheapsort(v, &mut a[pl as usize..=pr as usize]);
        } else {
            while pr - pl > SMALL_QUICKSORT {
                let pm = pl + ((pr - pl) >> 1);
                if np_less(v[a[pm as usize]], v[a[pl as usize]]) {
                    a.swap(pm as usize, pl as usize);
                }
                if np_less(v[a[pr as usize]], v[a[pm as usize]]) {
                    a.swap(pr as usize, pm as usize);
                }
                if np_less(v[a[pm as usize]], v[a[pl as usize]]) {
                    a.swap(pm as usize, pl as usize);
                }
                let vp = v[a[pm as usize]];
                let mut pi = pl;
                let mut pj = pr - 1;
                a.swap(pm as usize, pj as usize);
                loop {
                    loop {
                        pi += 1;
                        if !np_less(v[a[pi as usize]], vp) {
                            break;
                        }
                    }
                    loop {
                        pj -= 1;
                        if !np_less(vp, v[a[pj as usize]]) {
                            break;
                        }
                    }
                    if pi >= pj {
                        break;
                    }
                    a.swap(pi as usize, pj as usize);
                }
                let pk = pr - 1;
                a.swap(pi as usize, pk as usize);
                if pi - pl < pr - pi {
                    stack.push((pi + 1, pr));
                    pr = pi - 1;
                } else {
                    stack.push((pl, pi - 1));
                    pl = pi + 1;
                }
                cdepth -= 1;
                depth.push(cdepth);
            }
            // Insertion sort
            let mut pi = pl + 1;
            while pi <= pr {
                let vi = a[pi as usize];
                let vpv = v[vi];
                let mut pj = pi;
                let mut pk = pi - 1;
                while pj > pl && np_less(vpv, v[a[pk as usize]]) {
                    a[pj as usize] = a[pk as usize];
                    pj -= 1;
                    pk -= 1;
                }
                a[pj as usize] = vi;
                pi += 1;
            }
        }
        match stack.pop() {
            None => break,
            Some((l, r)) => {
                pl = l;
                pr = r;
                cdepth = depth.pop().unwrap_or(0);
            }
        }
    }
    tosort
}

/// NumPy `aheapsort_` on an index range.
fn aheapsort(v: &[f64], tosort: &mut [usize]) {
    let n0 = tosort.len();
    if n0 < 2 {
        return;
    }
    // 1-based indexing as in NumPy: a[k] = tosort[k-1]
    let get = |t: &[usize], k: usize| t[k - 1];
    let mut n = n0;
    let mut l = n >> 1;
    while l > 0 {
        let tmp = get(tosort, l);
        let mut i = l;
        let mut j = l << 1;
        while j <= n {
            if j < n && np_less(v[get(tosort, j)], v[get(tosort, j + 1)]) {
                j += 1;
            }
            if np_less(v[tmp], v[get(tosort, j)]) {
                tosort[i - 1] = get(tosort, j);
                i = j;
                j += j;
            } else {
                break;
            }
        }
        tosort[i - 1] = tmp;
        l -= 1;
    }
    while n > 1 {
        let tmp = get(tosort, n);
        tosort[n - 1] = get(tosort, 1);
        n -= 1;
        let mut i = 1;
        let mut j = 2;
        while j <= n {
            if j < n && np_less(v[get(tosort, j)], v[get(tosort, j + 1)]) {
                j += 1;
            }
            if np_less(v[tmp], v[get(tosort, j)]) {
                tosort[i - 1] = get(tosort, j);
                i = j;
                j += j;
            } else {
                break;
            }
        }
        tosort[i - 1] = tmp;
    }
}

/// `np.median` of a 1-D series (without NaN).
pub fn median(a: &[f64]) -> f64 {
    let n = a.len();
    if n == 0 {
        return f64::NAN;
    }
    if a.iter().any(|v| v.is_nan()) {
        return f64::NAN;
    }
    let mut s = a.to_vec();
    s.sort_by(|x, y| x.partial_cmp(y).unwrap_or(std::cmp::Ordering::Equal));
    if n % 2 == 1 { s[n / 2] } else { mean(&s[n / 2 - 1..n / 2 + 1]) }
}

/// `np.linspace(start, stop, num)` (endpoint=True).
pub fn linspace(start: f64, stop: f64, num: usize) -> Vec<f64> {
    if num == 0 {
        return Vec::new();
    }
    let div = (num as f64) - 1.0;
    let delta = stop - start;
    let step = delta / div;
    let mut y: Vec<f64> = (0..num).map(|i| i as f64).collect();
    if step == 0.0 {
        for v in y.iter_mut() {
            *v = (*v / div) * delta;
        }
    } else {
        for v in y.iter_mut() {
            *v *= step;
        }
    }
    for v in y.iter_mut() {
        *v += start;
    }
    if num > 1 {
        y[num - 1] = stop;
    }
    y
}

/// `float(np.clip(x, lo, hi))` for scalars.
pub fn clip(x: f64, lo: f64, hi: f64) -> f64 {
    // np.clip = minimum(maximum(x, lo), hi), NaN stays NaN
    let m = if x.is_nan() || lo.is_nan() {
        f64::NAN
    } else if x >= lo {
        x
    } else {
        lo
    };
    if m.is_nan() || hi.is_nan() {
        f64::NAN
    } else if m <= hi {
        m
    } else {
        hi
    }
}
