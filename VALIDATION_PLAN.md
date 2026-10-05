# Machine validation plan (draft for review)

Status: **draft, not approved**. Nothing in this plan has been done.
`motion_ready = false` until every phase below is signed off by you.

Who does what: **you** operate the machine and record results. Claude does not
connect to the controller, open its serial port, or send any command. Where a
step says "send", you type it yourself in your own sender.

Values in `[brackets]` are decisions for you. They are left blank on purpose:
no clearances, tolerances or offsets are assumed here.

---

## Phase 0 – Safety before any test (every session)

- [ ] No hand, person or object under the head. Bed clear.
- [ ] Valve / dispenser disconnected or depressurised. Nothing loaded.
- [ ] A physical emergency stop that cuts motor power is within reach, and you
      have tested that it stops motion.
- [ ] Feed override set low in your sender; start every move from a high Z.
- [ ] Start a **new session** in the log (see `validation_session_log.csv`).
      Never reuse values from earlier sessions or the old PC.

Stop the session immediately if anything moves unexpectedly. Log what happened.

## Phase 1 – Record the controller state (read-only)

Purpose: know exactly what the controller is set to before changing anything.

1. Send `$$` and save the full output to the session folder.
2. Send `$#` and save it (shows G54–G59, G92 and tool-length offsets).
3. Send `?` a few times with the machine idle and save the status lines,
   including any `Pn:` field.
4. Send `$I` and save the build info.

Record especially: `$3` (direction invert), `$5` (limit pin invert),
`$20` (soft limits), `$21` (hard limits), `$22` (homing), `$23` (homing
direction), `$100–$102` (steps/mm), `$130–$132` (max travel).

Pass when: all outputs are saved with the date and session ID.

## Phase 2 – Find why `Pn` is unresolved

Purpose: `Pn:` lists inputs GRBL sees as active (X/Y/Z limits, P probe, D door,
H hold, R reset, S start). A pin that is always active can stop homing or
limits from working.

1. With nothing pressed, note which letters appear in `Pn:`.
2. Press each limit switch by hand, one at a time, and note the change.
3. For any letter that is active with nothing pressed: check wiring, the
   `$5` invert setting, and whether that input is connected at all.

Pass when: with nothing pressed, `Pn:` is empty (or only lists inputs you
expect and can explain), and each switch shows the right letter when pressed.

## Phase 3 – Why Z travelled too far

Purpose: find the cause of the earlier descent into the bed and the soft-limit
failure. Do this with the nozzle and any pointer **removed**, and a block of
soft foam on the bed.

1. **Homing.** Home the machine (`$H`). Soft limits only work after a
   successful homing cycle. Note whether homing completes on all axes.
2. **Direction.** From a high Z, jog Z by a small amount in the "up" direction.
   Confirm it moves up. If not, the `$3` setting or wiring is wrong.
3. **Distance.** Jog Z by `[a distance you choose]` and measure the real travel
   with a ruler or dial gauge. Compare with the command. A mismatch means
   `$102` (Z steps/mm) or the drive setting is wrong.
4. **Coordinates.** Compare the `$#` output with how the earlier descent
   command was written. Check for G92 or G54 offsets, or a G53/G90/G91 mix-up,
   that would move Z further than intended.
5. **Soft limits.** With `$20=1` after homing, command a move **upwards** past
   the Z limit first, then sideways past an X limit. Confirm GRBL refuses with
   an alarm instead of moving. Only test the downward Z limit once you have
   passed every check above, with foam in place.

Pass when: Z direction and distance match the commands within `[your
tolerance]`, offsets are understood, and soft limits refuse an over-travel
command. Write down the cause of the earlier descent, or "not found".

## Phase 4 – Z reference with the real nozzle

Purpose: know where the bed is in machine Z using the **nozzle tip**, not the
pencil.

1. Fit the real nozzle (dispenser still disconnected).
2. Slowly lower to a sheet of paper on the bed until it just drags. Record the
   machine Z (MPos).
3. Raise, move to `[number]` different bed positions, and repeat.
4. Repeat the first position again at the end.

Pass when: repeated readings at the same place agree within `[your
tolerance]`. The spread across the bed shows its tilt.

## Phase 5 – Camera-to-nozzle offset (fresh session)

Purpose: measure where the nozzle tip is relative to the camera, which moves
with the head.

1. Put a small printed target (a dot or cross) on the bed.
2. Lower the nozzle above the target until it's centred, without touching.
   Record the machine position (MPos).
3. Raise to the usual capture height, move the head so the target is visible,
   and record that head position.
4. Record a short colour + depth file at that position (head still), named
   with the session ID. The prototype can then find the target in camera
   millimetres.
5. Repeat at `[number]` different positions of the target.

The offset is then calculated from: target in camera frame + head position at
capture versus nozzle position over the target. This calculation is **not
built yet**. It would be a new, separately approved step.

Pass when: the offset from different positions agrees within `[your
tolerance]`.

## Phase 6 – Head position at every capture

From now on, every recording needs the head position written in the session
log at the moment of recording (MPos X, Y, Z, read with the machine idle).
Without it, camera points can never be mapped to the machine.

## Phase 7 – Only after phases 0–6 pass

A separate G-code component can then be proposed. It must stay separate from
the preview code. Its first output would be **simulation only**: shown in a
viewer, never sent to the controller. A real dry run in the air, with no hand,
would follow only after you have reviewed that simulation.

---

## Sign-off

| Phase | Done by | Date | Session ID | Result | Notes |
|---|---|---|---|---|---|
| 0 Safety | | | | | |
| 1 Controller state | | | | | |
| 2 Pn | | | | | |
| 3 Z fault / soft limits | | | | | |
| 4 Z reference | | | | | |
| 5 Camera-to-nozzle | | | | | |
| 6 Head pose logging | | | | | |
