# Hand-labeled diarization references

Create two 10-15 minute council annotations after listening to the source:

1. An orderly discussion with clear turns.
2. A public-comment, interruption, or overlap-heavy interval.

Keep timestamps relative to the full meeting. For a region from 900 to 1500
seconds:

```text
# region.uem
2026-02-25-council 1 900.000 1500.000
```

```text
# reference.rttm
SPEAKER 2026-02-25-council 1 902.100 8.400 <NA> <NA> PERSON_01 <NA> <NA>
SPEAKER 2026-02-25-council 1 910.500 4.200 <NA> <NA> PERSON_02 <NA> <NA>
```

Labels only need to be internally consistent. Include simultaneous RTTM lines
for overlapping speech. Then add these fields to the matching meeting:

```yaml
reference_rttm: references/orderly-council/reference.rttm
uem: references/orderly-council/region.uem
```

The scorer reports strict DER (`collar=0.0`, overlap included), confusion,
missed detection, false alarm, and DER with a 0.25-second collar.
