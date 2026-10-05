# Session S2026-10-05-A results (nozzle, dispenser disconnected)

Copies of the accepted results (originals stay in `outputs/`, which is not tracked).

| File | What | Key numbers |
|---|---|---|
| nozzle_xy_calibration_20261005-114607.json | camera-to-nozzle X/Y, pose 1 | fit 1.56 mm RMS, scale 1.011, t (-52.0, 31.1) |
| nozzle_xy_verification_20261005-115122.json | same calibration tested at pose 2 | prediction 1.79 mm RMS, worst 2.90 |
| nozzle_z_calibration_20261005-121427.json | camera depth -> nozzle contact Z | k = 141.1 mm, spread 0.3, residual 0.14 mm RMS |
| nozzle_z_calibration_overlay.png | where the 3 block touches were found | |

Not used: 20261005-114242-calib (mislabelled clicks, 32.8 mm), 20261005-114833-calib (fit on pose 2 only).
Open: Z scale check 0.978 (Z axis vs camera depth not separated). motion_ready=false.
