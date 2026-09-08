/**
 * Engine-turned ornament, drawn rather than imitated.
 *
 * A geometric lathe traces a hypotrochoid: a point held at distance `d` from
 * the centre of a small wheel of radius `r` rolling inside a ring of radius
 * `R`. Repeating the trace at rotated phases is what makes a rosette. The
 * border strip is the same idea unrolled: interwoven sinusoids whose period
 * divides the tile, so it repeats seamlessly.
 *
 * Both return `url("data:image/svg+xml,...")` ready for `background-image`.
 */

const encode = (svg: string) => `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;

const wrap = (w: number, h: number, stroke: string, width: number, body: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
  `<g fill="none" stroke="${stroke}" stroke-width="${width}">${body}</g></svg>`;

/**
 * One traced curve. The teeth are integers so the curve closes exactly after
 * `r / gcd(R, r)` turns; floats leave a visible seam where the pen misses its
 * start. Pen distance `d` under the rolling radius gives petals, over it gives
 * loops — the difference between lathe work and a scribble.
 */
function trace(
  cx: number,
  cy: number,
  scale: number,
  R: number,
  r: number,
  d: number,
  phase = 0,
  /**
   * Compared at the size these actually render: 2400 and 1000 are
   * indistinguishable, 700 is very slightly faceted, and at 500 the lobes go
   * visibly polygonal. 1000 costs 21 KB against 58 KB for the same picture.
   */
  samples = 1000,
) {
  const turns = r / gcd(R, r);
  const k = (R - r) / r;
  const points: string[] = [];
  for (let i = 0; i <= samples; i++) {
    const t = (2 * Math.PI * turns * i) / samples + phase;
    const x = cx + scale * ((R - r) * Math.cos(t) + d * Math.cos(k * t));
    const y = cy + scale * ((R - r) * Math.sin(t) - d * Math.sin(k * t));
    // One decimal is under a tenth of a pixel at these sizes and saves ~15%.
    points.push(`${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`);
  }
  return `<path d="${points.join(' ')}"/>`;
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

/**
 * The underprint: an open interlaced rosette, laid faintly beneath a field as
 * on real security stock. 53 teeth against 8 is the classic open ring — dense
 * enough to read as engine-turning, open enough that type sits on it.
 */
export function rosette(size = 320, stroke = '#0b6b4f', width = 0.5) {
  const c = size / 2;
  const rings: [number, number, number, number][] = [
    [53, 8, 21, 0.46],
    [37, 6, 14, 0.24],
  ];
  const body = rings
    .map(([R, r, d, extent]) => trace(c, c, (size * extent) / (R - r + d), R, r, d))
    .join('');
  return encode(wrap(size, size, stroke, width, body));
}

/**
 * A single open ring for the grader's seal. Pen distance well under the
 * rolling radius keeps the band narrow and the centre clear, so the mark's
 * lettering sits inside the lathe work rather than on top of it.
 */
export function sealRing(size = 132, stroke = '#0b6b4f', width = 0.6) {
  const c = size / 2;
  const [R, r, d] = [53, 8, 11];
  const body =
    trace(c, c, (size * 0.47) / (R - r + d), R, r, d) +
    `<circle cx="${c}" cy="${c}" r="${(size * 0.475).toFixed(2)}"/>`;
  return encode(wrap(size, size, stroke, width, body));
}

/** The border: interwoven waves, two full periods per tile so it repeats. */
export function strip(vertical: boolean, stroke = '#0b6b4f', tile = 120, depth = 22) {
  const w = vertical ? depth : tile;
  const h = vertical ? tile : depth;
  const mid = depth / 2;
  const wave = (amp: number, periods: number, phase: number, opacity: number) => {
    const samples = 200;
    const points: string[] = [];
    for (let i = 0; i <= samples; i++) {
      const t = (tile * i) / samples;
      const off = mid + amp * Math.sin((2 * Math.PI * periods * i) / samples + phase);
      const [x, y] = vertical ? [off, t] : [t, off];
      points.push(`${i === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`);
    }
    return `<path d="${points.join(' ')}" opacity="${opacity}"/>`;
  };
  const body =
    wave(7.4, 2, 0, 1) +
    wave(7.4, 2, Math.PI, 1) +
    wave(3.4, 6, Math.PI / 2, 0.65) +
    wave(1.2, 12, 0, 0.4);
  return encode(wrap(w, h, stroke, 0.85, body));
}
