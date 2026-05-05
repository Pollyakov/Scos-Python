"""
Generate a synthetic 16-bit multipage TIFF for SCOS pipeline testing.

The pixel values follow a Rayleigh distribution, which resembles real speckle
statistics (amplitude of two independent Gaussian fields). This is good enough
to test that the processing pipeline runs correctly and to measure timing —
it is not intended to produce biologically meaningful κ² values.

Memory note: 700×700 × uint16 × 1200 frames ≈ 1.2 GB RAM at generation time.
For large stacks, this may be tight. Reduce --frames if needed.

Example:
    python tools/synth_tiff.py --out scratch/mock.tif --frames 1200
    python main.py --mock-tiff scratch/mock.tif
"""

import argparse
import numpy as np
import tifffile


def generate(width: int, height: int, frames: int,
             mean: int = 30000, seed: int = 0) -> np.ndarray:
    """
    Return a (frames, height, width) uint16 array with Rayleigh-distributed values.
    The Rayleigh scale parameter is chosen so that E[X] ≈ mean.
    """
    rng   = np.random.default_rng(seed)
    sigma = mean / np.sqrt(np.pi / 2)
    stack = rng.rayleigh(scale=sigma, size=(frames, height, width))
    return np.clip(stack, 0, 65535).astype(np.uint16)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out",    required=True,         help="Output .tif path")
    p.add_argument("--frames", type=int, default=1200, help="Number of frames (default 1200)")
    p.add_argument("--width",  type=int, default=700,  help="Frame width in pixels (default 700)")
    p.add_argument("--height", type=int, default=700,  help="Frame height in pixels (default 700)")
    p.add_argument("--mean",   type=int, default=30000,
                   help="Target mean pixel value in DU (default 30000). "
                        "Use ~2000 to simulate Mono12 with typical exposure.")
    p.add_argument("--seed",   type=int, default=0,    help="Random seed (default 0)")
    args = p.parse_args()

    mb = args.frames * args.height * args.width * 2 / 1024**2
    print(f"Generating {args.frames}×{args.height}×{args.width} uint16  ({mb:.0f} MB) …")
    stack = generate(args.width, args.height, args.frames, args.mean, args.seed)
    tifffile.imwrite(args.out, stack, photometric="minisblack")
    print(f"Saved: {args.out}")


if __name__ == "__main__":
    main()
