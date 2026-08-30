# P2 stream audit and scope-selection report

- State: PASSED
- Observed at (UTC): 2026-08-02T16:22:25+00:00
- Dataset snapshot ID: 44eface81cccdceb36c3fd61f93736267e63b84ba63902fac026659540518846
- Script Git revision: ebe9803425931c9594ed4729550fba752fae8e6f
- Audit SHA-256: 23f001beb0c856b840b9d37b97a43dcdd35ba0051a2c8fe0a858c1fb93284f40

## Data grain and validation

- Metadata grain: one source JSON object per metadata line; duplicate/conflicting parent keys are counted and excluded from candidate product counts.
- Review grain: one source JSON object per review line; joins are exact on parent_asin.
- Inputs were re-hashed against the frozen P1 manifest before parsing.

## Aggregate quality profile

| Source | Rows | Malformed | Joined | Unmatched | Valid ratings | Timestamps | Valid verified purchase | Valid helpful vote |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| metadata | 94327 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| reviews | 2128605 | 0 | 2100939 | 27666 | 2128605 | 2128605 | 2128605 | 2128605 |

- Conflicting metadata parent keys excluded: 0

## Candidate comparison

| Category | Products | Joined reviews | Join coverage | Text coverage | Rating coverage | Timestamp coverage | Eligible | Score |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| Water Filters | 5897 | 328861 | 0.999830 | 0.999659 | 1.000000 | 1.000000 | true | 0.995264 |
| Replacement Parts | 13785 | 272509 | 1.000000 | 0.999222 | 1.000000 | 1.000000 | true | 0.994780 |
| Parts & Accessories | 17628 | 149805 | 1.000000 | 0.999459 | 1.000000 | 1.000000 | true | 0.991586 |
| Reusable Filters | 2942 | 217634 | 1.000000 | 0.999458 | 1.000000 | 1.000000 | true | 0.980410 |
| Ice Makers | 3278 | 136604 | 1.000000 | 0.998675 | 1.000000 | 1.000000 | true | 0.979388 |
| Accessories | 3063 | 95883 | 0.999674 | 0.998352 | 1.000000 | 1.000000 | true | 0.971393 |
| Washer Parts & Accessories | 3578 | 53216 | 0.999721 | 0.999511 | 1.000000 | 1.000000 | true | 0.970672 |
| Disposable Filters | 1544 | 123234 | 1.000000 | 0.999708 | 1.000000 | 1.000000 | true | 0.958160 |
| Range Hoods | 2063 | 38019 | 1.000000 | 0.996975 | 1.000000 | 1.000000 | true | 0.950838 |
| Portable Washers | 1274 | 53608 | 1.000000 | 0.997724 | 1.000000 | 1.000000 | true | 0.947656 |
| Knobs | 2100 | 29296 | 1.000000 | 0.998327 | 1.000000 | 1.000000 | true | 0.945033 |
| Drain Pumps | 1624 | 33495 | 1.000000 | 0.999612 | 1.000000 | 1.000000 | true | 0.941027 |
| Refrigerators | 2135 | 22010 | 1.000000 | 0.998046 | 1.000000 | 1.000000 | true | 0.933428 |
| Vents | 1014 | 38271 | 1.000000 | 0.999190 | 1.000000 | 1.000000 | true | 0.919065 |
| Baskets | 1078 | 34709 | 1.000000 | 0.998444 | 1.000000 | 1.000000 | true | 0.915723 |
| Egg Trays | 1174 | 22472 | 1.000000 | 0.996485 | 1.000000 | 1.000000 | true | 0.912716 |
| Replacement Wicks | 1118 | 25902 | 1.000000 | 0.999730 | 1.000000 | 1.000000 | true | 0.911781 |
| Handles | 1252 | 19514 | 0.999201 | 0.996003 | 1.000000 | 1.000000 | true | 0.906476 |
| Hoses | 1386 | 15822 | 1.000000 | 0.997472 | 1.000000 | 1.000000 | true | 0.905499 |
| Dishwasher Parts & Accessories | 1169 | 20761 | 1.000000 | 0.999229 | 1.000000 | 1.000000 | true | 0.905246 |
| Bins | 1003 | 22998 | 1.000000 | 0.998869 | 1.000000 | 1.000000 | true | 0.897246 |
| Shelves | 1079 | 17346 | 1.000000 | 0.997694 | 1.000000 | 1.000000 | true | 0.887380 |
| Drip Pans | 622 | 28358 | 1.000000 | 0.999013 | 1.000000 | 1.000000 | true | 0.886544 |
| Cooktops | 1267 | 14126 | 1.000000 | 0.996319 | 1.000000 | 1.000000 | true | 0.884009 |
| Cooktop Parts & Accessories | 1809 | 9791 | 1.000000 | 0.999081 | 1.000000 | 1.000000 | true | 0.882162 |
| Refrigerator Parts & Accessories | 1112 | 15609 | 1.000000 | 0.999295 | 1.000000 | 1.000000 | true | 0.878835 |
| Motors | 1153 | 13143 | 1.000000 | 0.999543 | 1.000000 | 1.000000 | true | 0.872412 |
| Filters | 897 | 15793 | 1.000000 | 0.998607 | 1.000000 | 1.000000 | true | 0.870334 |
| Milk Frothing Pitchers | 689 | 19272 | 1.000000 | 0.998651 | 1.000000 | 1.000000 | true | 0.869371 |
| Doors | 944 | 14950 | 1.000000 | 0.999130 | 1.000000 | 1.000000 | true | 0.864404 |
| Oven Parts & Accessories | 589 | 15747 | 1.000000 | 0.999238 | 1.000000 | 1.000000 | true | 0.853697 |
| Humidifier Parts & Accessories | 696 | 14130 | 1.000000 | 0.999858 | 1.000000 | 1.000000 | true | 0.852434 |
| Freezer Parts & Accessories | 703 | 11231 | 1.000000 | 0.999021 | 1.000000 | 1.000000 | true | 0.843522 |
| Beverage Refrigerators | 547 | 11358 | 1.000000 | 0.997095 | 1.000000 | 1.000000 | true | 0.832058 |
| Burners | 483 | 8853 | 1.000000 | 0.997854 | 1.000000 | 1.000000 | true | 0.808980 |
| Freestanding Ranges | 824 | 4105 | 0.998786 | 0.999513 | 1.000000 | 1.000000 | true | 0.801235 |
| Humidity Meters | 411 | 8758 | 1.000000 | 0.999657 | 1.000000 | 1.000000 | true | 0.799496 |
| Washers | 538 | 6028 | 1.000000 | 0.999502 | 1.000000 | 1.000000 | true | 0.796760 |
| Range Parts & Accessories | 555 | 4830 | 1.000000 | 0.998758 | 1.000000 | 1.000000 | true | 0.795744 |
| Dryers | 509 | 6082 | 1.000000 | 0.998520 | 1.000000 | 1.000000 | true | 0.794739 |
| Countertop Dishwashers | 187 | 10642 | 1.000000 | 0.997745 | 1.000000 | 1.000000 | true | 0.794042 |
| Lint Screens | 432 | 6641 | 1.000000 | 0.999097 | 1.000000 | 1.000000 | true | 0.792952 |
| Upright Freezers | 277 | 8128 | 1.000000 | 0.997170 | 1.000000 | 1.000000 | true | 0.791694 |
| Portable Dryers | 197 | 9031 | 1.000000 | 0.997785 | 1.000000 | 1.000000 | true | 0.790818 |
| Chest Freezers | 293 | 6710 | 0.993174 | 0.997615 | 1.000000 | 1.000000 | true | 0.787166 |
| Heating Elements | 419 | 5664 | 1.000000 | 0.999823 | 1.000000 | 1.000000 | true | 0.782402 |
| Built-In Dishwashers | 516 | 3524 | 0.998062 | 0.999432 | 1.000000 | 1.000000 | true | 0.777370 |
| Bag Sealers | 233 | 4725 | 1.000000 | 0.997884 | 1.000000 | 1.000000 | true | 0.762602 |
| Stacked Washer & Dryer Units | 266 | 3089 | 1.000000 | 0.998381 | 1.000000 | 1.000000 | true | 0.756217 |
| Permanent Filters | 100 | 7897 | 1.000000 | 0.999747 | 1.000000 | 1.000000 | true | 0.754419 |
| Dryer Parts & Accessories | 123 | 4648 | 1.000000 | 0.999139 | 1.000000 | 1.000000 | true | 0.739083 |
| Fasteners | 162 | 2617 | 1.000000 | 0.998854 | 1.000000 | 1.000000 | true | 0.734001 |
| Single Wall Ovens | 247 | 1322 | 1.000000 | 0.999244 | 1.000000 | 1.000000 | true | 0.723465 |
| Kegerators | 175 | 1996 | 1.000000 | 0.998998 | 1.000000 | 1.000000 | true | 0.722460 |
| Exhaust Fans | 94 | 2784 | 1.000000 | 0.999641 | 1.000000 | 1.000000 | true | 0.715967 |
| Plug Receptacles | 104 | 2487 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | true | 0.715116 |
| Shrink Wrappers | 150 | 1456 | 1.000000 | 0.997253 | 1.000000 | 1.000000 | true | 0.709665 |
| Ranges, Ovens & Cooktops | 155 | 1199 | 1.000000 | 0.999166 | 1.000000 | 1.000000 | true | 0.703945 |
| Blowers | 70 | 2099 | 1.000000 | 0.999524 | 1.000000 | 1.000000 | true | 0.693210 |
| Burner Rings | 88 | 1314 | 1.000000 | 0.997717 | 1.000000 | 1.000000 | true | 0.685575 |
| Chemicals | 21 | 2845 | 1.000000 | 0.999649 | 1.000000 | 1.000000 | true | 0.679211 |
| Slide-In Ranges | 120 | 822 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | true | 0.675775 |
| Beverage Refrigerator Replacement Parts | 87 | 1015 | 1.000000 | 0.999015 | 1.000000 | 1.000000 | true | 0.675111 |
| Refrigerators, Freezers & Ice Makers | 125 | 644 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | true | 0.674871 |
| Washers & Dryers | 32 | 2040 | 1.000000 | 0.999020 | 1.000000 | 1.000000 | true | 0.674078 |
| Double Wall Ovens | 135 | 525 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | true | 0.671641 |
| Trash Compactor Parts & Accessories | 38 | 1581 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | true | 0.670995 |
| Grills | 60 | 1021 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | true | 0.664083 |
| Oven Racks | 80 | 828 | 1.000000 | 0.997585 | 1.000000 | 1.000000 | true | 0.661912 |
| Air Filters | 10 | 2233 | 1.000000 | 0.999552 | 1.000000 | 1.000000 | true | 0.661109 |
| All-in-One Combination Washers & Dryers | 62 | 985 | 1.000000 | 0.997970 | 1.000000 | 1.000000 | true | 0.660548 |
| Dishwashers | 84 | 612 | 1.000000 | 0.996732 | 1.000000 | 1.000000 | true | 0.652998 |
| Range Hood Parts & Accessories | 54 | 666 | 1.000000 | 0.996997 | 1.000000 | 1.000000 | true | 0.644640 |
| Absorption Sleeves | 27 | 175 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Air Conditioner Parts & Accessories | 9 | 101 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Combination Microwave & Wall Ovens | 41 | 287 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Drop-In Ranges | 4 | 18 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Food Waste Disposer Parts & Accessories | 5 | 312 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Freezers | 30 | 79 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Garbage Disposals & Compactors | 14 | 77 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Gerhards Appliances | 2 | 8 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Irons & Garment Steamers | 1 | 925 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Kegerator Replacement Parts | 10 | 30 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Laundry Appliances | 3 | 39 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| LG Styler Steam Closets | 1 | 9 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Portable Dishwashers | 25 | 293 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Ranges | 69 | 298 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Steam Cleaners & Steam Mops | 1 | 48 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Trash Compactors | 26 | 215 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| US MA 3P AWAS 2020 | 1 | 1 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Wall Ovens | 30 | 83 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |
| Warming Drawers | 15 | 18 | 1.000000 | 1.000000 | 1.000000 | 1.000000 | false | - |

## Selection rule

- Eligibility and weighted score use the frozen parameters in the JSON artifact.
- Recommended category: Water Filters

## Publication and interpretation boundary

- This report contains only aggregates. It omits review text, user identifiers, product identifiers, titles, source-record locations, and local absolute paths.
- Coverage and candidate score establish data suitability for this project scope only. They do not estimate product quality, defect rate, sales, or business impact.
