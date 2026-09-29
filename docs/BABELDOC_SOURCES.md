# BabelDOC External Evaluation Sources

This file records the external documents used in the additional BabelDOC extraction experiments so that the same PDFs can be downloaded and verified before reproducing the results.

## YOLOv3

**Document:** YOLOv3: An Incremental Improvement
**Authors:** Joseph Redmon and Ali Farhadi
**Source:** https://arxiv.org/pdf/1804.02767
**Expected filename:** `YOLOv3.pdf`

**SHA-256:**

`37049049b5e06f67c6cd22b72f7b9352914b0eb3a03e99abf54594c1005a8468`

Download:

```powershell
Invoke-WebRequest -Uri "https://arxiv.org/pdf/1804.02767" -OutFile "samples\YOLOv3.pdf"
```

Verify:

```powershell
Get-FileHash "samples\YOLOv3.pdf" -Algorithm SHA256
```

## TrafficSF

**Document:** Results of the 2013-2014 National Roadside Survey of Alcohol and Drug Use by Drivers
**Publisher:** National Highway Traffic Safety Administration
**Report:** DOT HS 812 118
**Date:** February 2015
**Source:** https://www.nhtsa.gov/sites/nhtsa.gov/files/812118-roadside_survey_2014.pdf
**Expected filename:** `TrafficSF.pdf`

**SHA-256:**

`b1bfc94aec044ff514c1621de6c2c8ef5cf4322199e11a3313988e328e269b32`

Download:

```powershell
Invoke-WebRequest -Uri "https://www.nhtsa.gov/sites/nhtsa.gov/files/812118-roadside_survey_2014.pdf" -OutFile "samples\TrafficSF.pdf"
```

Verify:

```powershell
Get-FileHash "samples\TrafficSF.pdf" -Algorithm SHA256
```

## Reference Status

`Sample02` remains the approved shared benchmark reference used for the main BabelDOC evaluation.

The additional annotations for:

- `Sample03`
- `Sample04`
- `YOLOv3`
- `TrafficSF`

are stored under:

`provisional-references/babeldoc/`

These references are provisional and require independent review before they can be moved into the shared `references/` directory.

The evaluation reports generated from these provisional references are stored under:

`evaluation-results/provisional/babeldoc/`

These provisional results are kept separate from the approved Sample02 benchmark results.
