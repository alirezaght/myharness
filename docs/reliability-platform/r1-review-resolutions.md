# R1 diagnostic review resolutions

Independent AL-Architect `/root/r1_fresh_review` inspected complete frozen source, selected diffs/new files and actual retained evidence, without running tests or services. Final target `/private/tmp/al-r1-frozen-review-003`; all1466 manifest hashes and E42 artifacts matched. All four diagnostic code findings are resolved. This is not formal AL approval or full mission completion.

| Finding | Actual repair and proof |
| --- | --- |
| R1-F01 overlapping list/detail requests | Separate request counters/loading state and current same-scope response routing. E36 and E42 exercise delayed detail plus parent refresh and both overlapping directions. |
| R1-F02 focus/disclosure loss | Stable summary IDs, stored disclosure state and focus/scroll restoration on every incident-content replacement. Real CDP keyboard/button/summary and collapsed disclosure checks pass at both widths. |
| R1-F03 older investigation lost on refresh | Passive refresh retains loaded history, selected evidence, focus and screen position. |
| R1-F04 hidden gaps and100-row cutoff | Passive refresh signals explicit Latest navigation without merging partial newest pages. Older always follows its cursor and slides a bounded100-row window, preserving complete forward traversal. E39 failed at52/57 and100/101; exact same command E42 passes57/57 and101/101, with zero missing records. |

Red and green XML, screenshots, scenario JSON and SHA-256 manifests are retained under [r1-evidence](r1-evidence/). The reviewer independently inspected the E42 1280×800 and390×844 screenshots and found no apparent clipping, horizontal overflow or material alignment issue; root also inspected both. The API privacy test now drives argument/result/exception canaries and their hashes through real HTTP. Snapshot tests distinguish immutable missing-package inventory compatibility from actual historical execution replay.

The repaired checks establish navigation scenarios, not an immutable database snapshot across concurrent inserts, which the keyset API does not promise. Existing native Claude capture qualification, broad source-bound CI/service coverage and formal fresh/comparison AL review remain verification obligations. Old passes that become stale remain stale; failed ledger records are unchanged.

The complete mission continues with R2 under protected session reliability-r2-active-20261119, run0d32430a. Earlier R2 preparation sessions admitted no writers and supply no PASS credit. R1 remains in its original run923dc63e with both recorded rework returns preserved.
