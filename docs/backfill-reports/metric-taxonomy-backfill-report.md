# Metric taxonomy backfill — dry-run report (ADR 034)

- Generated: 2026-08-30 20:02 UTC
- Model: `claude-haiku-4-5 (via subscription subagents)`
- Mode: DRY-RUN (no Sheet writes)
- **Provenance / method:** classified by **Claude Haiku 4.5 run via the Claude
  subscription** (8 parallel classification subagents over the 3,304 distinct
  `other`-row notes), applying the SAME vocabulary + trap guidance the production
  classifier uses. Every assigned metric was validated against
  `egle_doc_parser.METRIC_VALUES` (0 invalid). 16 notes whose keys the subagents
  retyped with drifted unicode (em/en-dash, `CH₄`/`CO₂`) were recovered by
  normalized matching (12) + 4 obvious manual corrections. This is a faithful
  DRY-RUN projection using the production model; a real `--apply` re-runs through
  the script's `messages.parse` structured-output path (which constrains the enum
  at the API layer) and may differ on a handful of genuinely-ambiguous edge notes.

## Headline (the success signal)

| | rows | % of tab |
|---|---:|---:|
| Total Measurements rows | 11336 | 100% |
| `other` BEFORE | 5127 | 45.2% |
| `other` AFTER (projected) | 149 | 1.3% |
| Rows reclassified | 4978 | 43.9% |

## Rows gained per newly-populated metric

| metric | rows moved in |
|---|---:|
| `pfas` | 605 |
| `hydrogen_sulfide` | 503 |
| `tss` | 322 |
| `sulfur_dioxide` | 308 |
| `ammonia_nitrogen` | 300 |
| `nitrogen_oxides` | 295 |
| `mercury` | 244 |
| `bod` | 209 |
| `ph` | 180 |
| `arsenic` | 173 |
| `methane` | 166 |
| `methane_secondary` | 150 |
| `phosphorus` | 148 |
| `operational_capacity` | 145 |
| `flow_wastewater` | 119 |
| `selenium` | 93 |
| `btex_chlorinated_voc` | 87 |
| `pressure_vacuum` | 77 |
| `nmoc_voc` | 69 |
| `dissolved_oxygen` | 68 |
| `nickel` | 59 |
| `surface_emissions` | 50 |
| `trs` | 49 |
| `hydrogen_gas` | 49 |
| `event_status` | 43 |
| `cyanide` | 40 |
| `well_operational` | 39 |
| `wind_odor` | 39 |
| `hydrogen_chloride` | 37 |
| `qa_sample` | 34 |
| `carbon_monoxide` | 28 |
| `oxygen` | 27 |
| `major_ions` | 21 |
| `exceedances_count` | 18 |
| `hardness` | 17 |
| `carbon_dioxide` | 17 |
| `temperature_secondary` | 15 |
| `pahs` | 15 |
| `cod` | 14 |
| `particulate_matter` | 12 |
| `toc` | 11 |
| `boron` | 10 |
| `barium` | 10 |
| `benzene` | 10 |
| `chromium` | 9 |
| `zinc` | 9 |
| `copper` | 8 |
| `lead` | 6 |
| `cadmium` | 6 |
| `alkalinity` | 4 |
| `combustion_efficiency` | 3 |
| `temperature` | 2 |
| `tds` | 2 |
| `ecoli_coliform` | 2 |
| `antimony` | 2 |

## Residual `other` (141 distinct notes still unplaceable)

These notes the classifier could not place on a named metric; they remain `other` by design (genuine fallback). Eyeball for anything that SHOULD have a metric:

- `2,4-D`
- `2,4-D (herbicide)`
- `AH East closed area`
- `AH West open area`
- `Acrylonitrile IRSL toxic screening value exceeding Rule 290 limit of 0.4`
- `Aeration ditch length`
- `Aeration ditch width`
- `Air compressor installed for pneumatic pumps`
- `Allowable exit velocity limit per 40 CFR 60.18(c)(4)(i)`
- `Allowable thermal discharge limit (exceeded 3 times during review period)`
- `Average excavation depth for contaminated sediment removal`
- `Average horizontal groundwater velocity range (lower bound) in Lower Aquifer Zone`
- `Average horizontal groundwater velocity range (upper bound) in Lower Aquifer Zone`
- `Average total PCB concentration in AHE raw leachate April 2019 – March 2020`
- `Balance gas (inert/other gases) concentration`
- `Balance gas concentration`
- `Barometric pressure at 10:23 AM`
- `Carbon absorption tank capacity for PFC wastewater treatment at Dart Disposal facility`
- `Cell 1 approved secondary collection system pump-on level`
- `Cell 1 secondary collection system approved pump-off level`
- `Cell 2 approved secondary collection system pump-on level`
- `Cell 2 leachate pump 1 reading at 2:20 pm`
- `Cell 2 leachate pump 2 reading at 2:20 pm`
- `Cell 2 leachate pump 3 reading at 2:20 pm`
- `Cell 3 approved secondary collection system pump-on level`
- `Cell 4 approved secondary collection system pump-on level`
- `Cell 5 approved secondary collection system pump-on level`
- `Clay soil supplementation depth in temporary cap area`
- `Combined HAPs PTE at 2,600 scfm`
- `Combined HAPs PTE at 4,600 scfm`
- `Combined HAPs PTE at 5,000 scfm`
- `Condensate processed August 2021`
- `Condensate processed May 2021`
- `Daily soil cover applied (requirement is 6 inches)`
- `Daily stipulated penalty rate for violation of CJ Paragraph 5.17(C)–(E)`
- `Day 1 dosage concentration (initial application of 45 gallons)`
- `Depth of leachate in well; well sounding 145 feet total`
- `Depth of leachate in well; well sounding 150 feet total`
- `Depth of leachate in well; well sounding 43 feet total`
- `Depth of vadose zone soil samples collected during VAP Study; PFAS not detected`
- `Diesel per turbine`
- `Distance from landfill to complaint location (Ridge and Powell)`
- `Dosing rate of Vitastim Nitrifiers`
- `Estimated moisture content for EUENCLOSEDFLARE1-S2`
- `Estimated moisture content for EUENCLOSEDFLARE2-S2`
- `Estimated total volume in stormwater pond`
- `Estimated total water volume in pond`
- `Example from instructions, not in document`
- `Extent of methane plume with Level 3 odors on 6 Mile Road`
- `FCV (Final Chronic Value) / WQBEL for Pit Raider`
- `Federal SEM threshold above background (40 CFR 63.1958)`
- `Final Chronic Value (FCV) for Vitastim Nitrifiers`
- `Flare exhaust gas exit velocity`
- `Flare inlet gas net heating value`
- `Flare minimum operating temperature`
- `Flare minimum retention time`
- `Flooded area at its wettest extent; stormwater pond adjacent to compost leachate pond`
- `Freshwater Aquatic Value (FAV) for Vitastim Nitrifiers`
- `Increase in gas collection rate since beginning of January 2016`
- `Interim cover slope grade (equals 25 percent)`
- `LC50 (lethal concentration) for fathead minnow, calculated from highest dose tested`
- `Leachate depth in 145-foot well sounding`
- `Leachate depth in 150-foot well sounding; excess temperature exceedance`
- `Length of 6-inch perforated horizontal gas collection pipe installed September 15, 2023`
- `Length of aeration ditch`
- `MSW and C&D waste collection rate`
- `Maximum allowable exit velocity limit`
- `Maximum permitted velocity (V_max) per 40 CFR 60.18(c)(4)(iii)`
- `Maximum sulfur content in fuel for turbines`
- `Michigan Human Noncancer Value (HNV) for non-drinking water — regulatory threshold`
- `Minimum destruction temperature for thermal oxidizer EURNGTOX`
- `Minimum net heating value for non-assisted flares`
- `Minimum net heating value requirement per 40 CFR 60.18(c)(3)(ii)`
- `Minimum required net heating value`
- `Monthly application dosage concentration`
- `Net heating value minimum for non-assisted flares`
- `Net heating value minimum for steam-assisted or air-assisted flares`
- `Net heating value of landfill gas being combusted`
- `Nitrafix concentration in 4,000,000-gallon treatment pond`
- `Nitrafix concentration in pond (calculated from 10 gallons in 4 million gallons over 10 days)`
- `Nitrogen reading from H&N balance gas data`
- `N₂ composition in landfill gas, Run No. 2`
- `Oil and grease`
- `Phenol in leachate effluent`
- `Phenols, sample AH (not detected)`
- `Pit Raider discharge concentration on Day 1; exceeds WQBEL limit`
- `Pit Raider requested discharge concentration (average over 3-4 months)`
- `Proposed compensatory mitigation wetland (PEM 3.21 ac + PFO 3.28 ac)`
- `Proposed discharge concentration for Pit Raider via Outfall 001`
- `Pump Daily Avg - Dec 1, 2022Q4`
- `Pump Daily Avg - Dec 2, 2022Q4`
- `Pump Daily Avg - Nov 1, 2022Q4`
- `Pump Daily Avg - Nov 2, 2022Q4`
- `Pump Daily Avg - Oct 1, 2022Q4`
- `Pump Daily Avg - Oct 2, 2022Q4`
- `Pump Daily Avg - Sep 1, 2022Q4; units unclear from table context`
- `Pump Daily Avg - Sep 2, 2022Q4`
- `RACM (Type II Waste) estimated for removal`
- `Recommended Heat Addition Limit for March`
- `Regulatory maximum leachate head depth on primary liner, R 299.4432(1)`
- `Relative humidity at 10:23 AM`
- `Report limit for phenolics analysis`
- `Required minimum enclosed flare stack height above ground`
- `Screen submersion threshold - wells with >50% screen submerged by liquid or obstruction trigger corrective action consideration`
- `Silver, Total`
- `Solid pipe length threshold - wells with >30 feet of solid pipe without open screen may require replacement`
- `Stipulated penalty amount per Consent Judgment CJ No. 2020-0593-CE Paragraph 13.4 ($750/day for 2 days of noncompliance)`
- `Sulfur content of LFG sample from final test results`
- `Sulfur content of fuel maximum limit`
- `Surface water elevation difference between Wetland 1 and Pond 3 (May 2026 survey)`
- `Synthetic cover (GCL 40 mil composite) tarped at south end of landfill protective cover`
- `Temporary cap area on lower northwest slope`
- `Thermal Discharge maximum monthly average`
- `Thermal Discharge, Maximum Monthly Average`
- `Thermal discharge maximum (March)`
- `Thermal oxidizer minimum retention time`
- `Thermal oxidizer minimum temperature`
- `This value appears in the schema instructions as an example but is NOT present in the actual document text; included per schema requirement but document contains no temperature readings`
- `Total PCB maximum concentration in AHE raw leachate (PCB-1232, PCB-1242, PCB-1248, PCB-1254)`
- `Total Phenol in Discharge 001A, not detected above 10.0 limit`
- `Total Phenolics in leachate effluent`
- `Total combined HAPs for all three flares`
- `Total diesel`
- `Total phenolics - non-detected (ND) at reporting limit, NPDES discharge sample Discharge 001A`
- `Total phenolics, water discharge sample DISCHARGE 001A - GRAB`
- `Total phenols reporting limit (RL); actual measured result: not detected`
- `Total sulfur content in landfill gas per Jet-Care fuel analysis`
- `Turbine fuel sulfur content limit`
- `Typical slope of 25 percent or greater for cover slopes outside active operational area`
- `Visible emission limit for EU5000CFMFLARE`
- `Vitastim Nitrifiers discharge concentration approved`
- `Vitastim Nitrifiers discharge concentration limit, Outfall 001`
- `Water Quality-Based Effluent Limit (WQBEL) for Pit Raider`
- `Weather at 8:30–9:00 AM on inspection day`
- `Weekly application dosage concentration for 3 weeks`
- `Wetland 1 area affected (PEM and PFO combined)—permanent impact`
- `fuel sulfur content limit for turbines`
- `humidity`
- `minimum flare temperature requirement`
- `minimum retention time in flare`
- `phenol — below reporting limit`

## Full note → metric mapping (the reversible record)

Every distinct note among the `other` rows and the metric it was assigned. Old value is uniformly `other`, so this table fully determines every row's remap.

| distinct note | assigned metric |
|---|---|
| `1,1-Dichloroethene, intermediate stage` | `btex_chlorinated_voc` |
| `1,1-dichloroethane, ONYX-INF sample` | `btex_chlorinated_voc` |
| `1,1-dichloroethene` | `btex_chlorinated_voc` |
| `1,1-dichloroethene daily discharge limitation` | `btex_chlorinated_voc` |
| `1,1-dichloroethene discharge limit` | `btex_chlorinated_voc` |
| `1,1-dichloroethene discharge limitation` | `btex_chlorinated_voc` |
| `1,1-dichloroethene limit` | `btex_chlorinated_voc` |
| `1,1-dichloroethylene maximum daily` | `btex_chlorinated_voc` |
| `100 feet north of WW 266 from bare ground; methane` | `methane` |
| `100 feet north of WW 281R from crack in ground; methane` | `methane` |
| `100 feet northeast of WW 281R; methane` | `methane` |
| `100 feet northeast of manifold hill from bare spot; methane` | `methane` |
| `100 feet south of WW 289; methane` | `methane` |
| `100 feet west of WW 237R3; methane` | `methane` |
| `100 yards west of well M7; methane` | `methane` |
| `12-month rolling NOx emission limit for FGPROJECT` | `nitrogen_oxides` |
| `12-month rolling SO2 emission limit for FGPROJECT` | `sulfur_dioxide` |
| `12-month rolling average concentration limit for total mercury` | `mercury` |
| `12-month rolling average limit for total mercury` | `mercury` |
| `12-month rolling average mercury influent concentration, 2015` | `mercury` |
| `18 unofficial SEM hits above 500 ppm methane` | `exceedances_count` |
| `2 blowers currently installed and operating` | `operational_capacity` |
| `2,4-D` | `other` |
| `2,4-D (herbicide)` | `other` |
| `20 feet north of WW 258R2; methane` | `methane` |
| `200 feet east of manifold hill from crack in ground; methane` | `methane` |
| `2014 average influent mercury concentration` | `mercury` |
| `25 feet east of WW 451; methane` | `methane` |
| `4-point rolling average mercury influent concentration, 2015` | `mercury` |
| `4:2 FTSA in DAF effluent` | `pfas` |
| `4:2 FTSA in leachate DAF effluent` | `pfas` |
| `50 feet north of HW12 from bare ground; methane` | `methane` |
| `5:3FTCA in stormwater sample AH-STORM WATER` | `pfas` |
| `6:2 FTSA (6:2 Fluorotelomer Sulfonic Acid) in wastewater outfall` | `pfas` |
| `6:2 FTSA (6:2 fluorotelomer sulfonic acid) in leachate DAF effluent` | `pfas` |
| `6:2 FTSA in DAF Effluent leachate sample` | `pfas` |
| `6:2 FTSA in DAF effluent` | `pfas` |
| `6:2FTS in stormwater sample AH-STORM WATER` | `pfas` |
| `6:2FTS in wastewater Outfall-001A` | `pfas` |
| `8:2 FTSA (8:2 fluorotelomer sulfonic acid) in leachate DAF effluent` | `pfas` |
| `8:2 FTSA in DAF Effluent leachate sample` | `pfas` |
| `8:2 FTSA in DAF effluent` | `pfas` |
| `ADS flow meter reading before adjustment` | `operational_capacity` |
| `ADS meter reading at start of first test run` | `operational_capacity` |
| `AH East closed area` | `other` |
| `AH West open area` | `other` |
| `AHE burning approximately 10,000 scfm of landfill gas at record levels` | `operational_capacity` |
| `AHE plant gas flow at 51 mole% CH4, 1.6% O2` | `methane_secondary` |
| `AQSI flow at start of first test run (11:15 AM)` | `operational_capacity` |
| `AQSI sampling equipment flow rate reading before adjustment` | `operational_capacity` |
| `Acrylonitrile IRSL toxic screening value exceeding Rule 290 limit of 0.4` | `other` |
| `Action level alarm threshold for H2S at perimeter monitors` | `hydrogen_sulfide` |
| `Actual NOx emissions for Emerald RNG facility` | `nitrogen_oxides` |
| `Actual PM10 emissions` | `particulate_matter` |
| `Actual PM2.5 emissions` | `particulate_matter` |
| `Additional gallons to be removed for freeboard` | `flow_wastewater` |
| `Aeration ditch length` | `other` |
| `Aeration ditch width` | `other` |
| `Aeration pond capacity per pond` | `operational_capacity` |
| `Aggregate blower flow verified to date; gas collection capacity` | `operational_capacity` |
| `Air compressor installed for pneumatic pumps` | `other` |
| `Air pressure 11/4/2022` | `pressure_vacuum` |
| `Air pressure at top of Hill (high range)` | `pressure_vacuum` |
| `Air pressure at top of Hill (low range)` | `pressure_vacuum` |
| `Air pressure at well 11/4/2022` | `pressure_vacuum` |
| `Air pressure measured 11/4/2022` | `pressure_vacuum` |
| `Air pressure measured 11/7/2022` | `pressure_vacuum` |
| `Air pressure measured 12/22/2022` | `pressure_vacuum` |
| `Air pressure pump installed 1/17/2023` | `pressure_vacuum` |
| `Air pressure upon pump replacement` | `pressure_vacuum` |
| `Air temperature at inspection time` | `temperature` |
| `Alkalinity-Bicarbonate, Sample AH-FC` | `alkalinity` |
| `Alkalinity-Bicarbonate, Sample AH-GC` | `alkalinity` |
| `Alkalinity-Total, Sample AH-FC` | `alkalinity` |
| `Alkalinity-Total, Sample AH-GC` | `alkalinity` |
| `Allowable exit velocity limit per 40 CFR 60.18(c)(4)(i)` | `other` |
| `Allowable thermal discharge limit (exceeded 3 times during review period)` | `other` |
| `Ambient air temperature` | `temperature_secondary` |
| `Ambient air temperature at time of inspection` | `temperature_secondary` |
| `Ambient air temperature at time of survey` | `temperature_secondary` |
| `Ammonia NPDES permitted maximum monthly average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (Average)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (Maximum)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) (00610) - Maximum Monthly Average permit requirement` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) - Final Effluent (1)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) - Maximum Daily` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) - Maximum Monthly Average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) at MP001A effluent` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) at monitoring point 001A` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) average, Final Effluent; permit limit 10 mg/L` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) daily maximum limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) in leachate effluent` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N) max daily` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), Final Effluent (1); permit max 10 mg/L` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), Final Effluent, daily maximum—exceeds 6.4 mg/L limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), Final Effluent, monthly average—exceeds 5.4 mg/L limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), Final Effluent; permit limit 10 mg/L` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), Maximum Daily permit limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), average concentration` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), maximum` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), maximum concentration` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), maximum daily limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N), maximum monthly average limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N); maximum daily` | `ammonia_nitrogen` |
| `Ammonia Nitrogen (as N); monthly average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - Average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - Average concentration` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - Maximum` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - Maximum Daily` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - Maximum Daily limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - Maximum Monthly Average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - Maximum Monthly Average limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - Maximum concentration` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - Monthly Average Limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen - maximum (grab sample)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen Maximum Daily` | `ammonia_nitrogen` |
| `Ammonia Nitrogen Maximum Daily limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen Maximum Daily permitted limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen Maximum Monthly Average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen Maximum Monthly Average permit limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen Maximum Monthly Average permitted limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen May-November daily maximum` | `ammonia_nitrogen` |
| `Ammonia Nitrogen as N (00610) - Maximum Daily` | `ammonia_nitrogen` |
| `Ammonia Nitrogen as N (00610) - Maximum Monthly Average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen as N, average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen as N, maximum` | `ammonia_nitrogen` |
| `Ammonia Nitrogen average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen average (second entry)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen average concentration` | `ammonia_nitrogen` |
| `Ammonia Nitrogen average concentration, EXCEEDS limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen daily maximum, December-April` | `ammonia_nitrogen` |
| `Ammonia Nitrogen daily maximum, May-November` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum (EXCEEDANCE)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum concentration` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum daily` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum daily (permit limit 8.4 lbs/day)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum daily concentration` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum daily limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum daily limit (loading)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum daily loading limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum daily loading; permit limit 8.4 lbs/day` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum daily; permit limit 10 mg/L` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum monthly average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum monthly average concentration` | `ammonia_nitrogen` |
| `Ammonia Nitrogen maximum monthly average limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen minimum` | `ammonia_nitrogen` |
| `Ammonia Nitrogen monthly average limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen monthly average limit (April)` | `ammonia_nitrogen` |
| `Ammonia Nitrogen permit maximum monthly average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen permitted maximum monthly average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, April 2014, Outfall 001` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, December 2013, Outfall 001` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, February 2014, Outfall 001` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, Final Effluent, exceeds 6.4 mg/l limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, Final Effluent, violation` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, Final Effluent, violation of 6.4 mg/l limit` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, March 2014, Outfall 001` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, Maximum Daily` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, Maximum Monthly Average` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, Outfall 001` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, permit limit 0.5 mg/l` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, permit limit 10 mg/L` | `ammonia_nitrogen` |
| `Ammonia Nitrogen, permit limit 6.4 mg/l` | `ammonia_nitrogen` |
| `Ammonia Nitrogen—Maximum Daily` | `ammonia_nitrogen` |
| `Ammonia Nitrogen—Maximum Monthly Average` | `ammonia_nitrogen` |
| `Ammonia as N - Outfall-001A Composite sample` | `ammonia_nitrogen` |
| `Ammonia as N in Outfall 001A composite wastewater` | `ammonia_nitrogen` |
| `Ammonia as N, Outfall 001A` | `ammonia_nitrogen` |
| `Ammonia as N, Outfall 001A Composite` | `ammonia_nitrogen` |
| `Ammonia as N, Outfall-001A Comp` | `ammonia_nitrogen` |
| `Ammonia as N, Outfall-001A Composite` | `ammonia_nitrogen` |
| `Ammonia as Nitrogen` | `ammonia_nitrogen` |
| `Ammonia as Nitrogen - Influent Pond` | `ammonia_nitrogen` |
| `Ammonia as Nitrogen in sample P3-6 (W)` | `ammonia_nitrogen` |
| `Ammonia as nitrogen maximum monthly average permit limit` | `ammonia_nitrogen` |
| `Ammonia as nitrogen monthly average concentration, Remediation Area discharge` | `ammonia_nitrogen` |
| `Ammonia as nitrogen monthly average loading, December 2020` | `ammonia_nitrogen` |
| `Ammonia nitrogen at Final Effluent, exceeds permit limit of 6.4 mg/l` | `ammonia_nitrogen` |
| `Ammonia nitrogen average` | `ammonia_nitrogen` |
| `Ammonia nitrogen concentration` | `ammonia_nitrogen` |
| `Ammonia nitrogen concentration (alternate)` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum concentration` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum daily` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum daily (May-Nov)` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum daily limit` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum daily loading` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum monthly (April)` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum monthly (Jan-Mar, Dec)` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum monthly (May-Nov)` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum monthly average` | `ammonia_nitrogen` |
| `Ammonia nitrogen maximum monthly average (alternate)` | `ammonia_nitrogen` |
| `Ammonia nitrogen permit requirement maximum monthly average` | `ammonia_nitrogen` |
| `Ammonia nitrogen, Final Effluent, Apr–Nov maximum monthly average` | `ammonia_nitrogen` |
| `Ammonia nitrogen, Final Effluent, Jan–Mar, Dec maximum monthly average` | `ammonia_nitrogen` |
| `Ammonia nitrogen, permit limit 0.4 lbs/day` | `ammonia_nitrogen` |
| `Ammonia nitrogen, permit limit 0.5 mg/l` | `ammonia_nitrogen` |
| `Ammonia permit limit` | `ammonia_nitrogen` |
| `Ammonia, DAF Effluent sample CV09033` | `ammonia_nitrogen` |
| `Ammonia, East Pond` | `ammonia_nitrogen` |
| `Ammonia, Mid GAC sample CV09034` | `ammonia_nitrogen` |
| `Ammonia, West Pond` | `ammonia_nitrogen` |
| `Ammonia, discharge range April 2010 (before aeration)` | `ammonia_nitrogen` |
| `Ammonia, discharge range January 2010 (before aeration)` | `ammonia_nitrogen` |
| `Ammonia, maximum daily concentration December 2010` | `ammonia_nitrogen` |
| `Ammonia, monthly average pond discharge post-aeration` | `ammonia_nitrogen` |
| `Ammonia-N, Sample AH-FC` | `ammonia_nitrogen` |
| `Ammonia-N, Sample AH-GC` | `ammonia_nitrogen` |
| `Ammonia-Nitrogen effluent concentration` | `ammonia_nitrogen` |
| `Ammonia; all diffusers operational` | `ammonia_nitrogen` |
| `Ammonia; diffusers not operational at time of sampling` | `ammonia_nitrogen` |
| `Ammonia; exceeds permit limit of 5.4 lbs/day` | `ammonia_nitrogen` |
| `Ammonia; exceeds permit limit of 6.4 mg/L` | `ammonia_nitrogen` |
| `Ammonia; permit limit 5.4 lbs/day` | `ammonia_nitrogen` |
| `Ammonia; permit limit 6.4 mg/L` | `ammonia_nitrogen` |
| `Annual NOx limit for EUTURBINE4` | `nitrogen_oxides` |
| `Annual diesel fuel used for turbine start-up in 2018` | `event_status` |
| `Approximately 150 odor complaints from nearby residential areas` | `wind_odor` |
| `Arbor Hills Energy gas-to-energy turbine plant capacity` | `operational_capacity` |
| `Arbor Hills RNG CSF maximum flare capacity` | `operational_capacity` |
| `Arbor Hills Remediation Area authorized continuous discharge flow` | `flow_wastewater` |
| `Arsenic - measured data point` | `arsenic` |
| `Arsenic - recommended monthly limit` | `arsenic` |
| `Arsenic PEL (FCV monthly avg)` | `arsenic` |
| `Arsenic PEQ` | `arsenic` |
| `Arsenic discharge limit` | `arsenic` |
| `Arsenic in influent` | `arsenic` |
| `Arsenic in influent; WQS/FCV 10 ug/L` | `arsenic` |
| `Arsenic in leachate effluent` | `arsenic` |
| `Arsenic monthly average loading (incorrectly reported; should be 0.0015)` | `arsenic` |
| `Arsenic monthly maximum loading (incorrectly reported; should be 0.0019)` | `arsenic` |
| `Arsenic recommended monthly limit` | `arsenic` |
| `Arsenic, Sample AH` | `arsenic` |
| `Arsenic, Sample AH-FC` | `arsenic` |
| `Arsenic, Sample AH-GC` | `arsenic` |
| `Arsenic, sample SED-08` | `arsenic` |
| `Arsenic-Total, ONYX-001 sample` | `arsenic` |
| `Arsenic-Total, ONYX-001-1 sample` | `arsenic` |
| `Arsenic-Total, ONYX-001-2 sample` | `arsenic` |
| `Arsenic-Total, ONYX-COMPOST sample` | `arsenic` |
| `Asbestos waste accepted in January 2019` | `event_status` |
| `Authorized discharge flow at Monitoring Point 001A` | `flow_wastewater` |
| `Authorized discharge flow from Monitoring Point 001A` | `flow_wastewater` |
| `Authorized discharge flow rate from Monitoring Point 001A` | `flow_wastewater` |
| `Authorized discharge limit for treated groundwater from Monitoring Point 001A through Outfall 001` | `flow_wastewater` |
| `Authorized discharge limit for treated groundwater through Monitoring Point 001A` | `flow_wastewater` |
| `Authorized discharge volume, Outfall 001A` | `flow_wastewater` |
| `Authorized groundwater discharge volume from Monitoring Point 001A` | `flow_wastewater` |
| `Authorized maximum treated groundwater discharge from Monitoring Point 001A through Outfall 001` | `flow_wastewater` |
| `Available Cyanide` | `cyanide` |
| `Available Cyanide - January monthly grab` | `cyanide` |
| `Available Cyanide - Maximum Daily limit` | `cyanide` |
| `Available Cyanide - Maximum Monthly Average limit` | `cyanide` |
| `Available Cyanide daily maximum` | `cyanide` |
| `Available Cyanide maximum daily` | `cyanide` |
| `Available Cyanide maximum daily limit` | `cyanide` |
| `Available Cyanide maximum monthly average` | `cyanide` |
| `Available Cyanide, Final Effluent` | `cyanide` |
| `Available Cyanide, Final Effluent (1)` | `cyanide` |
| `Available Cyanide, Final Effluent (1); permit max 44 ug/L` | `cyanide` |
| `Available Cyanide; permit limit 44 ug/L` | `cyanide` |
| `Available cyanide daily limit` | `cyanide` |
| `Available cyanide in final effluent` | `cyanide` |
| `Available cyanide maximum daily` | `cyanide` |
| `Available cyanide maximum daily limit` | `cyanide` |
| `Available cyanide maximum daily loading` | `cyanide` |
| `Available cyanide monthly limit` | `cyanide` |
| `Available cyanide – single highest detection` | `cyanide` |
| `Available vacuum at north-slope wells` | `pressure_vacuum` |
| `Average daily flow January 2008 to July 2013` | `flow_wastewater` |
| `Average discharge rate` | `flow_wastewater` |
| `Average excavation depth for contaminated sediment removal` | `other` |
| `Average flow rate during bypass` | `flow_wastewater` |
| `Average horizontal groundwater velocity range (lower bound) in Lower Aquifer Zone` | `other` |
| `Average horizontal groundwater velocity range (upper bound) in Lower Aquifer Zone` | `other` |
| `Average landfill gas flow for turbine operation` | `operational_capacity` |
| `Average observed flow rate during bypass` | `flow_wastewater` |
| `Average total PCB concentration in AHE raw leachate April 2019 – March 2020` | `other` |
| `BETX (ethylbenzene, toluene, xylenes)` | `btex_chlorinated_voc` |
| `BETX daily discharge limitation` | `btex_chlorinated_voc` |
| `BETX max monthly` | `btex_chlorinated_voc` |
| `BETX, influent` | `btex_chlorinated_voc` |
| `BETX, intermediate stage` | `btex_chlorinated_voc` |
| `BOD (Biochemical Oxygen Demand), permit limit 3 lbs/day` | `bod` |
| `BOD Carbonaceous 5-day (less than detection limit), Outfall 001A Composite` | `qa_sample` |
| `BOD carbonaceous concentration limit` | `bod` |
| `BOD carbonaceous concentration maximum daily` | `bod` |
| `BOD carbonaceous maximum daily` | `bod` |
| `BOD carbonaceous maximum monthly average` | `bod` |
| `BOD effluent, exceeded 3 lbs/day limit` | `bod` |
| `BOD, Carbonaceous (5-Day) (below detection limit, estimated)` | `bod` |
| `BOD, permit limit 4 mg/l` | `bod` |
| `BOD5 (Biochemical Oxygen Demand)` | `bod` |
| `BOD5 in compost pond effluent` | `bod` |
| `BOD5 maximum monthly concentration - conventional pollutant` | `bod` |
| `BTEX, influent and effluent` | `btex_chlorinated_voc` |
| `Backup blower and flare capacity if Fortistar plant goes offline` | `operational_capacity` |
| `Balance gas` | `methane_secondary` |
| `Balance gas (inert/other gases) concentration` | `other` |
| `Balance gas (nitrogen/air)` | `methane_secondary` |
| `Balance gas concentration` | `other` |
| `Bare ground/ditch just west of prior location; methane` | `methane` |
| `Barium in Outfall 001A composite wastewater` | `barium` |
| `Barium in wastewater sample MP001A` | `barium` |
| `Barium, Outfall 001A` | `barium` |
| `Barium, Outfall 001A Composite` | `barium` |
| `Barium, Sample AH` | `barium` |
| `Barium, Total` | `barium` |
| `Barometric pressure at 10:23 AM` | `other` |
| `Benzene (below detection limit)` | `benzene` |
| `Benzene daily discharge limitation` | `benzene` |
| `Benzene discharge limit` | `benzene` |
| `Benzene discharge limitation` | `benzene` |
| `Benzene limit` | `benzene` |
| `Benzene max monthly` | `benzene` |
| `Benzene maximum daily` | `benzene` |
| `Benzene, ONYX-INT sample` | `benzene` |
| `Benzene, ethylbenzene, toluene, xylene combined maximum daily` | `btex_chlorinated_voc` |
| `Benzo(a)anthracene` | `pahs` |
| `Benzo(a)pyrene` | `pahs` |
| `Benzo(k)fluoranthene` | `pahs` |
| `Biochemical Oxygen Demand (BOD), Carbonaceous 5-day, below detection limit <2.0` | `bod` |
| `Biochemical Oxygen Demand (BOD5) max daily from May 2005–May 2006` | `bod` |
| `Biochemical Oxygen Demand Carbonaceous 5-day, Outfall 001A Composite` | `bod` |
| `Biochemical Oxygen Demand, Carbonaceous 5-day - below detection limit (< 2.0), estimated per quality flag` | `bod` |
| `Biochemical Oxygen Demand, Carbonaceous 5-day in Outfall 001A composite wastewater; <2.0 (below RDL); estimated per QC note BS01` | `bod` |
| `Biochemical oxygen demand (5-day), Outfall-001A Comp, below detection` | `bod` |
| `Boron in Outfall 001A composite wastewater` | `boron` |
| `Boron in wastewater sample MP001A` | `boron` |
| `Boron, Outfall 001A` | `boron` |
| `Boron, Outfall 001A Composite` | `boron` |
| `Boron, Sample AH` | `boron` |
| `Boron, Total` | `boron` |
| `Bullet tank capacity (two units); carbon filtration removed during upgrade` | `operational_capacity` |
| `CBOD effluent concentration` | `bod` |
| `CBOD reporting limit, sample AH-GC (not detected)` | `qa_sample` |
| `CBOD, sample AH-FC` | `bod` |
| `CBOD5` | `bod` |
| `CBOD5 (80082) - Maximum Daily` | `bod` |
| `CBOD5 (80082) - Maximum Monthly Average` | `bod` |
| `CBOD5 (Average)` | `bod` |
| `CBOD5 (Maximum)` | `bod` |
| `CBOD5 (monthly average), May 2005, Outfall 001; exceeded limit of 3.0 lbs/day` | `bod` |
| `CBOD5 (monthly average), May 2005, Outfall 001; exceeded limit of 4.0 mg/L` | `bod` |
| `CBOD5 - Daily Maximum Limit` | `bod` |
| `CBOD5 - Daily Maximum Limit (concentration)` | `bod` |
| `CBOD5 - Final Effluent (1)` | `bod` |
| `CBOD5 - Maximum` | `bod` |
| `CBOD5 - Maximum Daily` | `bod` |
| `CBOD5 - Maximum Daily limit` | `bod` |
| `CBOD5 - Maximum Monthly Average` | `bod` |
| `CBOD5 - Maximum Monthly Average limit` | `bod` |
| `CBOD5 - Maximum concentration` | `bod` |
| `CBOD5 - average` | `bod` |
| `CBOD5 - maximum` | `bod` |
| `CBOD5 30-day average, Arbor Hills, May–September (AWT)` | `bod` |
| `CBOD5 Maximum Daily` | `bod` |
| `CBOD5 Maximum Daily limit` | `bod` |
| `CBOD5 Maximum Daily permit limit` | `bod` |
| `CBOD5 Maximum Daily permitted limit` | `bod` |
| `CBOD5 Maximum Monthly Average` | `bod` |
| `CBOD5 Maximum Monthly Average permitted limit` | `bod` |
| `CBOD5 May-November daily maximum` | `bod` |
| `CBOD5 at MP001A effluent` | `bod` |
| `CBOD5 at monitoring point 001A, below detection` | `bod` |
| `CBOD5 average` | `bod` |
| `CBOD5 concentration average` | `bod` |
| `CBOD5 daily max, Arbor Hills, May–September (AWT)` | `bod` |
| `CBOD5 daily maximum, December-March/April` | `bod` |
| `CBOD5 daily maximum, May-November` | `bod` |
| `CBOD5 maximum` | `bod` |
| `CBOD5 maximum concentration` | `bod` |
| `CBOD5 maximum daily` | `bod` |
| `CBOD5 maximum daily (Jan-Mar, Dec)` | `bod` |
| `CBOD5 maximum daily (May-Nov)` | `bod` |
| `CBOD5 maximum daily (permit limit 37 lbs/day)` | `bod` |
| `CBOD5 maximum daily concentration` | `bod` |
| `CBOD5 maximum daily limit` | `bod` |
| `CBOD5 maximum daily limit (loading)` | `bod` |
| `CBOD5 maximum daily loading` | `bod` |
| `CBOD5 maximum daily loading limit` | `bod` |
| `CBOD5 maximum daily loading; permit limit 37 lbs/day` | `bod` |
| `CBOD5 maximum daily; permit limit 44 mg/L` | `bod` |
| `CBOD5 maximum monthly (May-Nov)` | `bod` |
| `CBOD5 maximum monthly average` | `bod` |
| `CBOD5 maximum monthly average concentration` | `bod` |
| `CBOD5 maximum monthly average limit` | `bod` |
| `CBOD5 maximum; permitted limit 44 mg/L` | `bod` |
| `CBOD5 measured maximum` | `bod` |
| `CBOD5 permit maximum daily` | `bod` |
| `CBOD5 permit requirement maximum daily` | `bod` |
| `CBOD5 reporting limit (RL); actual measured result: not detected` | `qa_sample` |
| `CBOD5 spring daily max, Onyx Arbor Hills LF` | `bod` |
| `CBOD5 summer/fall 30-day avg, Onyx Arbor Hills LF` | `bod` |
| `CBOD5 summer/fall daily max, Onyx Arbor Hills LF` | `bod` |
| `CBOD5 winter/spring daily max, Onyx Arbor Hills LF` | `bod` |
| `CBOD5, Apr maximum daily` | `bod` |
| `CBOD5, Jan–Mar, Dec maximum daily` | `bod` |
| `CBOD5, Maximum Daily` | `bod` |
| `CBOD5, Maximum Monthly Average` | `bod` |
| `CBOD5, May 18, 2005, Outfall 001; exceeded limit of 10 mg/L` | `bod` |
| `CBOD5, May 18, 2005, Outfall 001; exceeded limit of 8.3 lbs/day` | `bod` |
| `CBOD5, May–Nov maximum daily` | `bod` |
| `CBOD5, May–Nov maximum monthly average` | `bod` |
| `CBOD5, maximum` | `bod` |
| `CBOD5, maximum daily limit` | `bod` |
| `CBOD5, maximum monthly average limit` | `bod` |
| `CBOD5, permit limit 44 mg/L` | `bod` |
| `CBOD5, permit requirement maximum` | `bod` |
| `CBOD5; maximum daily` | `bod` |
| `CBOD5—Maximum Daily` | `bod` |
| `CBOD5—Maximum Monthly Average` | `bod` |
| `CH4 (methane) perimeter monitor alarm threshold` | `methane` |
| `CH4 gas composition` | `methane_secondary` |
| `CH4 gas composition; east wells generally lower` | `methane_secondary` |
| `CH4 gas composition; excellent gas quality` | `methane_secondary` |
| `CH4 lowest detection` | `methane` |
| `CH4 maximum detection limit` | `methane` |
| `CH4 methane percentage` | `methane` |
| `CH4 methane percentage; very low reading` | `methane_secondary` |
| `CH₄ (methane) composition` | `methane_secondary` |
| `CH₄ composition` | `methane_secondary` |
| `CH₄ composition in landfill gas, Run No. 2` | `methane_secondary` |
| `CO 12-month limit` | `carbon_monoxide` |
| `CO 12-month rolling limit for EUOFRNG` | `carbon_monoxide` |
| `CO 12-month rolling limit for EURNGTOX` | `carbon_monoxide` |
| `CO 12-month rolling limit for EUTURBINE4` | `carbon_monoxide` |
| `CO 12-month rolling limit for FGPROJECT23` | `carbon_monoxide` |
| `CO 12-month rolling limit for FGTURBINES` | `carbon_monoxide` |
| `CO annual limit for EUTURBINE4` | `carbon_monoxide` |
| `CO calendar year 2020 MAERS report` | `carbon_monoxide` |
| `CO emission limit for EUOFRNG (12-month rolling)` | `carbon_monoxide` |
| `CO emission limit for EUOFRNG (hourly)` | `carbon_monoxide` |
| `CO emission limit for EURNGTOX (12-month rolling)` | `carbon_monoxide` |
| `CO emission limit for EURNGTOX (hourly)` | `carbon_monoxide` |
| `CO emission limit for FGENCLOSEDFLARES` | `carbon_monoxide` |
| `CO emission rate AHE Turbine 3` | `carbon_monoxide` |
| `CO emissions, 12-month rolling average for FGPROJECT23` | `carbon_monoxide` |
| `CO hourly emission limit for EURNGTOX` | `carbon_monoxide` |
| `CO hourly limit for EUOFRNG` | `carbon_monoxide` |
| `CO hourly limit for EUTURBINE4` | `carbon_monoxide` |
| `CO normal operation for FGTURBINES` | `carbon_monoxide` |
| `CO permit limit, combined turbines` | `carbon_monoxide` |
| `CO, Turbine 2 only mode, three-test average` | `carbon_monoxide` |
| `CO, Turbine 3 only mode, three-test average` | `carbon_monoxide` |
| `CO2 Post-run Mid gas` | `qa_sample` |
| `CO2 Post-run Zero gas` | `qa_sample` |
| `CO2 Pre-run Mid gas` | `qa_sample` |
| `CO2 Pre-run Zero gas` | `qa_sample` |
| `CO2, Run 1` | `carbon_dioxide` |
| `CO2, Run 2` | `carbon_dioxide` |
| `CO2, Run 3` | `carbon_dioxide` |
| `COD` | `cod` |
| `COD (Chemical Oxygen Demand)` | `cod` |
| `COD (Chemical Oxygen Demand), sediment intrusion resample` | `cod` |
| `COD in compost pond effluent` | `cod` |
| `COD reduction by Arbor Hills West new treatment system` | `cod` |
| `COD resample due to sediment intrusion` | `cod` |
| `CO₂ (carbon dioxide) composition` | `carbon_dioxide` |
| `CO₂ composition` | `carbon_dioxide` |
| `CO₂ composition in landfill gas, Run No. 2` | `carbon_dioxide` |
| `CO₂ concentration` | `carbon_dioxide` |
| `CO₂ concentration; indicates air intrusion` | `carbon_dioxide` |
| `Cadmium, Total` | `cadmium` |
| `Cairpol H2S reading near TS-01` | `hydrogen_sulfide` |
| `Calcium Hardness as CaCO3 calculated from sample MP001A` | `hardness` |
| `Calcium in Outfall 001A composite wastewater` | `major_ions` |
| `Calcium in wastewater sample MP001A` | `major_ions` |
| `Calcium, Outfall 001A` | `major_ions` |
| `Calcium, Outfall 001A Composite` | `major_ions` |
| `Calcium, Sample AH` | `major_ions` |
| `Calcium, Sample AH-FC` | `major_ions` |
| `Calcium, Sample AH-GC` | `major_ions` |
| `Calcium, Total` | `major_ions` |
| `Candlestick (new) blower test flow` | `operational_capacity` |
| `Candlestick 5000 scfm flare consumption during inspection visit` | `operational_capacity` |
| `Candlestick flare capacity for gas control system` | `operational_capacity` |
| `Candlestick flare flow rate per company data at 7:00 p.m.` | `operational_capacity` |
| `Candlestick flare rated capacity` | `operational_capacity` |
| `Capacity of each aeration pond` | `operational_capacity` |
| `Capacity per aeration pond` | `operational_capacity` |
| `Capacity per aeration pond (two ponds total)` | `operational_capacity` |
| `Carbon absorption tank capacity for PFC wastewater treatment at Dart Disposal facility` | `other` |
| `Carbon dioxide (CO2)` | `carbon_dioxide` |
| `Carbonaceous BOD 5-day, West Pond sample` | `bod` |
| `Carbonaceous BOD effluent, exceeded 4 mg/L limit` | `bod` |
| `Carbonaceous BOD5, Final Effluent (1); permit max 44 mg/L` | `bod` |
| `Carbonaceous Biochemical Oxygen Demand (CBOD5) (80082) - Maximum Daily permit requirement` | `bod` |
| `Carbonaceous Biochemical Oxygen Demand (CBOD5) daily maximum limit` | `bod` |
| `Carbonaceous Biochemical Oxygen Demand (CBOD5) maximum` | `bod` |
| `Carbonaceous Biochemical Oxygen Demand (CBOD5), average` | `bod` |
| `Carbonaceous Biochemical Oxygen Demand (CBOD5), maximum concentration` | `bod` |
| `Cell 1 Primary Pump OFF level` | `pressure_vacuum` |
| `Cell 1 Primary Pump ON level` | `pressure_vacuum` |
| `Cell 1 approved secondary collection system pump-on level` | `other` |
| `Cell 1 secondary collection system approved pump-off level` | `other` |
| `Cell 2 Primary Pump OFF level` | `pressure_vacuum` |
| `Cell 2 Primary Pump ON level` | `pressure_vacuum` |
| `Cell 2 Secondary Pump OFF level` | `pressure_vacuum` |
| `Cell 2 Secondary Pump ON level` | `pressure_vacuum` |
| `Cell 2 approved secondary collection system pump-on level` | `other` |
| `Cell 2 leachate pump 1 reading at 2:20 pm` | `other` |
| `Cell 2 leachate pump 2 reading at 2:20 pm` | `other` |
| `Cell 2 leachate pump 3 reading at 2:20 pm` | `other` |
| `Cell 2 secondary collection system flow rate August–November 2019` | `flow_wastewater` |
| `Cell 2 secondary collection system flow rate June & July 2019` | `flow_wastewater` |
| `Cell 3 Primary Pump OFF level` | `pressure_vacuum` |
| `Cell 3 Primary Pump ON level` | `pressure_vacuum` |
| `Cell 3 Secondary Pump OFF level` | `pressure_vacuum` |
| `Cell 3 Secondary Pump ON level` | `pressure_vacuum` |
| `Cell 3 approved secondary collection system pump-on level` | `other` |
| `Cell 4 Primary Pump OFF level` | `pressure_vacuum` |
| `Cell 4 Primary Pump ON level` | `pressure_vacuum` |
| `Cell 4 Secondary Pump OFF level` | `pressure_vacuum` |
| `Cell 4 Secondary Pump ON level` | `pressure_vacuum` |
| `Cell 4 approved secondary collection system pump-on level` | `other` |
| `Cell 4 primary leachate collection system violation threshold` | `event_status` |
| `Cell 4 secondary collection system flow rate June–September 2019` | `flow_wastewater` |
| `Cell 4 secondary collection system flow rate October 2019` | `flow_wastewater` |
| `Cell 5 Primary Pump OFF level` | `pressure_vacuum` |
| `Cell 5 Primary Pump ON level` | `pressure_vacuum` |
| `Cell 5 Secondary Pump OFF level` | `pressure_vacuum` |
| `Cell 5 Secondary Pump ON level` | `pressure_vacuum` |
| `Cell 5 approved secondary collection system pump-on level` | `other` |
| `Chemical Oxygen Demand (COD) - Influent Pond` | `cod` |
| `Chemical Oxygen Demand (COD) in NPDES Sample Johnson Drain` | `cod` |
| `Chemical Oxygen Demand (COD) in Outfall-CR sample` | `cod` |
| `Chemical Oxygen Demand, DAF Effluent sample CV09033` | `cod` |
| `Chemical Oxygen Demand, Mid GAC sample CV09034` | `cod` |
| `Chromium (permitted Direct Contact limit 1,000,000 ug/kg), sample SED-03` | `chromium` |
| `Chromium, Total` | `chromium` |
| `Chronic un-ionized ammonia (NH3-N) toxicity criterion` | `ammonia_nitrogen` |
| `Chrysene` | `pahs` |
| `Clay soil supplementation depth in temporary cap area` | `other` |
| `Closest distance of elevated methane band to perimeter monitors` | `event_status` |
| `Collection system efficiency per ADS (inspector expressed doubt)` | `operational_capacity` |
| `Combined HAPs PTE at 2,600 scfm` | `other` |
| `Combined HAPs PTE at 4,600 scfm` | `other` |
| `Combined HAPs PTE at 5,000 scfm` | `other` |
| `Combined flare capacity per stack test` | `operational_capacity` |
| `Combined maximum flaring capacity` | `operational_capacity` |
| `Combined rating of two enclosed flares at start of 2018` | `operational_capacity` |
| `Combined rating of two enclosed style backup flares` | `operational_capacity` |
| `Commonly assumed collection efficiency` | `operational_capacity` |
| `Compost leachate pond discharge volume requested in modification` | `flow_wastewater` |
| `Condensate processed August 2021` | `other` |
| `Condensate processed May 2021` | `other` |
| `Control panel reading during test runs (consistent 4300–4400 cfm range)` | `operational_capacity` |
| `Copper, Total` | `copper` |
| `Corrected lab bench sheet value (originally transcribed as 7.46)` | `ph` |
| `Cumulative duration of un-combusted gas release` | `event_status` |
| `Current in-effect permit authorized flow rate` | `flow_wastewater` |
| `Current landfill gas collection rate from facility` | `operational_capacity` |
| `Current landfill gas collection volume` | `operational_capacity` |
| `Current total mercury LCA limit, 12-month rolling average` | `mercury` |
| `Cyanide (Available) - Outfall-001A Grab sample` | `cyanide` |
| `Cyanide (Available), Outfall 001A` | `cyanide` |
| `Cyanide (Available), Outfall-001A Grab` | `cyanide` |
| `Cyanide (available), Outfall-001A Grab` | `cyanide` |
| `Cyanide Available, Outfall 001A Grab` | `cyanide` |
| `Cyanide, Available` | `cyanide` |
| `Daily average methane content (FGPROJECT23)` | `methane_secondary` |
| `Daily maximum TSS permit limit` | `tss` |
| `Daily soil cover applied (requirement is 6 inches)` | `other` |
| `Daily stipulated penalty rate for violation of CJ Paragraph 5.17(C)–(E)` | `other` |
| `Day 1 dosage concentration (initial application of 45 gallons)` | `other` |
| `Depth of leachate in well; well sounding 145 feet total` | `other` |
| `Depth of leachate in well; well sounding 150 feet total` | `other` |
| `Depth of leachate in well; well sounding 43 feet total` | `other` |
| `Depth of vadose zone soil samples collected during VAP Study; PFAS not detected` | `other` |
| `Diesel per turbine` | `other` |
| `Discharge Specific Level Currently Achievable (LCA) for total mercury` | `mercury` |
| `Dissolved Oxygen` | `dissolved_oxygen` |
| `Dissolved Oxygen (winter)` | `dissolved_oxygen` |
| `Dissolved Oxygen - Minimum Daily` | `dissolved_oxygen` |
| `Dissolved Oxygen - Minimum Daily Limit` | `dissolved_oxygen` |
| `Dissolved Oxygen - Minimum Daily limit` | `dissolved_oxygen` |
| `Dissolved Oxygen May-March minimum` | `dissolved_oxygen` |
| `Dissolved Oxygen Minimum Daily` | `dissolved_oxygen` |
| `Dissolved Oxygen Minimum Daily limit` | `dissolved_oxygen` |
| `Dissolved Oxygen Minimum Daily permitted limit` | `dissolved_oxygen` |
| `Dissolved Oxygen concentration` | `dissolved_oxygen` |
| `Dissolved Oxygen minimum` | `dissolved_oxygen` |
| `Dissolved Oxygen minimum daily` | `dissolved_oxygen` |
| `Dissolved Oxygen minimum daily limit` | `dissolved_oxygen` |
| `Dissolved Oxygen minimum daily requirement` | `dissolved_oxygen` |
| `Dissolved Oxygen minimum permit limit` | `dissolved_oxygen` |
| `Dissolved Oxygen permit minimum daily` | `dissolved_oxygen` |
| `Dissolved Oxygen, Final Effluent (1)` | `dissolved_oxygen` |
| `Dissolved Oxygen, Final Effluent (1); permit minimum 7.0 mg/L` | `dissolved_oxygen` |
| `Dissolved Oxygen, Final Effluent, violation of 7.0 mg/l minimum` | `dissolved_oxygen` |
| `Dissolved Oxygen, Minimum Daily` | `dissolved_oxygen` |
| `Dissolved Oxygen, permit limit 7.0 mg/l` | `dissolved_oxygen` |
| `Dissolved Oxygen, permit minimum 7.0 mg/L` | `dissolved_oxygen` |
| `Dissolved oxygen` | `dissolved_oxygen` |
| `Dissolved oxygen (DO) in AHE leachate` | `dissolved_oxygen` |
| `Dissolved oxygen minimum (April)` | `dissolved_oxygen` |
| `Dissolved oxygen minimum (Jan-Mar, May-Dec)` | `dissolved_oxygen` |
| `Dissolved oxygen minimum daily limit` | `dissolved_oxygen` |
| `Distance from Cell 6A filling area to addresses reporting odor complaints` | `wind_odor` |
| `Distance from landfill to complaint location (Ridge and Powell)` | `other` |
| `Dosing rate of Vitastim Nitrifiers` | `other` |
| `Duct Burner 1 SO2 annual (8760 hrs/yr basis)` | `sulfur_dioxide` |
| `Duct Burner 1 SO2 annual permitted limit` | `sulfur_dioxide` |
| `Duct Burner 1 SO2 emission rate` | `sulfur_dioxide` |
| `Duct Burner 1 SO2 permitted limit` | `sulfur_dioxide` |
| `Duct Burner 2 SO2 annual (8760 hrs/yr basis)` | `sulfur_dioxide` |
| `Duct Burner 2 SO2 annual permitted limit` | `sulfur_dioxide` |
| `Duct Burner 2 SO2 emission rate` | `sulfur_dioxide` |
| `Duct Burner 2 SO2 permitted limit` | `sulfur_dioxide` |
| `Duct Burner 3 SO2 annual (8760 hrs/yr basis)` | `sulfur_dioxide` |
| `Duct Burner 3 SO2 annual permitted limit` | `sulfur_dioxide` |
| `Duct Burner 3 SO2 emission rate` | `sulfur_dioxide` |
| `Duct Burner 3 SO2 permitted limit` | `sulfur_dioxide` |
| `Duct burner heat input capacity (each of 3 units)` | `operational_capacity` |
| `Ductburner 1 SO2 annual emission rate` | `sulfur_dioxide` |
| `Ductburner 1 SO2 emission rate` | `sulfur_dioxide` |
| `Ductburner 2 SO2 annual emission rate` | `sulfur_dioxide` |
| `Ductburner 2 SO2 emission rate` | `sulfur_dioxide` |
| `Ductburner 3 SO2 annual emission rate` | `sulfur_dioxide` |
| `Ductburner 3 SO2 emission rate` | `sulfur_dioxide` |
| `Ductburner SO2 annual permit limit` | `sulfur_dioxide` |
| `Ductburner SO2 permit limit` | `sulfur_dioxide` |
| `Duration of monitoring gap on FGENCLOSEDFLARES-S2; flow and temperature data not recorded every 15 minutes as required` | `event_status` |
| `E. coli` | `ecoli_coliform` |
| `EGLE Part 201 Soil criteria protective of drinking water for PFOS` | `pfas` |
| `EGLE Part 201 drinking water criterion for PFOS+PFOA` | `pfas` |
| `EGLE Part 201 soil criterion for PFOA (drinking water protective)` | `pfas` |
| `EGLE Part 201 soil criterion for PFOS (drinking water protective)` | `pfas` |
| `EGLE Rule 57 PFOA limit for non-drinking water source` | `pfas` |
| `EGLE Rule 57 PFOS limit for non-drinking water source` | `pfas` |
| `EGT Typhoon turbine maximum heat input design capacity` | `operational_capacity` |
| `ENE wind speed at 10:23 AM` | `wind_odor` |
| `EPA health advisory level for PFOA+PFOS in drinking water (combined)` | `pfas` |
| `EPA-reported baseline of wells with >50% screen submerged (2016)` | `well_operational` |
| `EU5000CFMFLARE flow average` | `operational_capacity` |
| `EUOFRNG (flare) NOx hourly emission limit` | `nitrogen_oxides` |
| `EUOFRNG CO emissions, 12-month rolling (limit 50.4 tons)` | `carbon_monoxide` |
| `EUOFRNG NOx 12-month rolling total limit` | `nitrogen_oxides` |
| `EUOFRNG NOx emissions, 12-month rolling (limit 11.05 tons)` | `nitrogen_oxides` |
| `EURNGPLANT capacity limit for landfill gas` | `operational_capacity` |
| `EURNGPLANT landfill gas processing capacity limit` | `operational_capacity` |
| `EURNGPLANT maximum landfill gas processing capacity` | `operational_capacity` |
| `EURNGPLANT total sulfur concentration limit at STS outlet` | `trs` |
| `EURNGTOX CO emissions, 12-month rolling (limit 37.0 tons)` | `carbon_monoxide` |
| `EURNGTOX CO rolling emissions Jan 2024 (limit 37.0 tons)` | `carbon_monoxide` |
| `EURNGTOX NOx 12-month rolling total limit` | `nitrogen_oxides` |
| `EURNGTOX NOx emissions, 12-month rolling (limit 11.1 tons)` | `nitrogen_oxides` |
| `EURNGTOX NOx hourly emission limit` | `nitrogen_oxides` |
| `EURNGTOX NOx rolling emissions Jan 2024 (limit 11.1 tons)` | `nitrogen_oxides` |
| `EUTURBINE1 Sulfur Dioxide limit` | `sulfur_dioxide` |
| `EUTURBINE1 sulfur dioxide limit (also applies to EUTURBINE2, EUTURBINE3)` | `sulfur_dioxide` |
| `EUTURBINE1-S3 alone, SO2 emissions` | `sulfur_dioxide` |
| `EUTURBINE1-S3 alone, SO2 limit` | `sulfur_dioxide` |
| `EUTURBINE1-S3 permitted SO₂ limit` | `sulfur_dioxide` |
| `EUTURBINE1-S3 with ductburner, SO2 emissions` | `sulfur_dioxide` |
| `EUTURBINE1-S3 with ductburner, SO2 limit` | `sulfur_dioxide` |
| `EUTURBINE2 Sulfur Dioxide limit` | `sulfur_dioxide` |
| `EUTURBINE3 Sulfur Dioxide limit` | `sulfur_dioxide` |
| `EUTURBINE3-S3 alone, SO2 emissions` | `sulfur_dioxide` |
| `EUTURBINE3-S3 alone, SO2 limit` | `sulfur_dioxide` |
| `EUTURBINE3-S3 permitted SO₂ limit` | `sulfur_dioxide` |
| `EUTURBINE3-S3 with ductburner, SO2 emissions` | `sulfur_dioxide` |
| `EUTURBINE3-S3 with ductburner, SO2 limit` | `sulfur_dioxide` |
| `EUTURBINE4 Nitrogen Oxides (NO2) limit` | `nitrogen_oxides` |
| `EUTURBINE4 Sulfur Dioxide limit` | `sulfur_dioxide` |
| `EUTURBINE4 hydrogen chloride annual limit` | `hydrogen_chloride` |
| `EUTURBINE4 hydrogen chloride limit` | `hydrogen_chloride` |
| `EUTURBINE4 nitrogen oxides (NO2) limit` | `nitrogen_oxides` |
| `EUTURBINE4 nitrogen oxides annual limit` | `nitrogen_oxides` |
| `EUTURBINE4 nitrogen oxides emission limit` | `nitrogen_oxides` |
| `EUTURBINE4 nitrogen oxides emission limit (12-month rolling)` | `nitrogen_oxides` |
| `EUTURBINE4 nitrogen oxides limit` | `nitrogen_oxides` |
| `EUTURBINE4 sulfur dioxide annual limit` | `sulfur_dioxide` |
| `EUTURBINE4 sulfur dioxide limit` | `sulfur_dioxide` |
| `EUTURBINE4 total VOC annual limit` | `nmoc_voc` |
| `EUTURBINE4 total VOC limit` | `nmoc_voc` |
| `Each pond capacity (two ponds)` | `operational_capacity` |
| `Effluent pH violation; limit was 9.0` | `ph` |
| `Effluent; limit 9.0` | `ph` |
| `Elevation in ground temperature per thermal camera spider pattern` | `temperature_secondary` |
| `Emerald RNG plant rated capacity` | `operational_capacity` |
| `Enclosed flare stack height` | `operational_capacity` |
| `Erroneous transcription on April 2011 daily DMR` | `ph` |
| `Estimated H₂S threshold above which Arbor Hills might exceed KKKK limit` | `hydrogen_sulfide` |
| `Estimated discharge volume through outfall 0001A based on average flow of 20 gpm over bypass period` | `flow_wastewater` |
| `Estimated leak from CO2/N2 removal equipment inside building` | `carbon_dioxide` |
| `Estimated moisture content for EUENCLOSEDFLARE1-S2` | `other` |
| `Estimated moisture content for EUENCLOSEDFLARE2-S2` | `other` |
| `Estimated temperature of escaping gases from condensate vent` | `temperature_secondary` |
| `Estimated total volume in stormwater pond` | `other` |
| `Estimated total water volume in pond` | `other` |
| `EtFOSAA in DAF Effluent leachate sample` | `pfas` |
| `EtFOSAA in DAF effluent` | `pfas` |
| `EtFOSAA in leachate DAF effluent` | `pfas` |
| `Ethylbenzene (below detection limit)` | `btex_chlorinated_voc` |
| `Example from instructions, not in document` | `other` |
| `Example surface methane exceedance location near EW65` | `methane` |
| `Exhaust CO2 content` | `carbon_dioxide` |
| `Expected landfill gas flow during test` | `operational_capacity` |
| `Extent of methane plume with Level 3 odors on 6 Mile Road` | `other` |
| `FCV (Final Chronic Value) / WQBEL for Pit Raider` | `other` |
| `FGDUCTBURNERS-S3 SO₂ permit limit` | `sulfur_dioxide` |
| `FGDUCTBURNERS-S3 SO₂ permit limit, annualized` | `sulfur_dioxide` |
| `FGNOX facility-wide nitrogen oxides limit` | `nitrogen_oxides` |
| `FGPROJECT NOx 12-month limit` | `nitrogen_oxides` |
| `FGPROJECT NOx emissions` | `nitrogen_oxides` |
| `FGPROJECT SOx 12-month limit` | `sulfur_dioxide` |
| `FGPROJECT SOx emissions` | `sulfur_dioxide` |
| `FGPROJECT VOC 12-month limit` | `nmoc_voc` |
| `FGPROJECT VOC emissions` | `nmoc_voc` |
| `FGTURBINES NOx 12-month limit` | `nitrogen_oxides` |
| `FGTURBINES NOx mass emissions` | `nitrogen_oxides` |
| `FGTURBINES-S3 NOx permit limit` | `nitrogen_oxides` |
| `FGTURBINES-S3 NOx permit limit, annualized` | `nitrogen_oxides` |
| `FGTURBINES-S3 SO₂ permit limit` | `sulfur_dioxide` |
| `FGTURBINES-S3 SO₂ permit limit, annualized` | `sulfur_dioxide` |
| `FM backpressure 11/4/2022` | `pressure_vacuum` |
| `FM backpressure at well 11/4/2022` | `pressure_vacuum` |
| `FM backpressure measured 11/4/2022` | `pressure_vacuum` |
| `FM backpressure measured 12/22/2022` | `pressure_vacuum` |
| `FM pressure measured 11/7/2022` | `pressure_vacuum` |
| `FM pressure pump installed 1/17/2023` | `pressure_vacuum` |
| `Facility goal for minimum gas flow` | `operational_capacity` |
| `Facility-wide NOx emission limit (12-month rolling time period)` | `nitrogen_oxides` |
| `Facility-wide SO2 total 12-month rolling` | `sulfur_dioxide` |
| `Fecal coliform` | `ecoli_coliform` |
| `Federal SEM threshold above background (40 CFR 63.1958)` | `other` |
| `Field meter ammonia concentration reading, east pond discharge` | `ammonia_nitrogen` |
| `Final Chronic Value (FCV) for Vitastim Nitrifiers` | `other` |
| `Final NOx test result` | `nitrogen_oxides` |
| `Final NOx test result in lb/hr` | `nitrogen_oxides` |
| `Final effluent pH maximum limit` | `ph` |
| `Final effluent pH minimum limit` | `ph` |
| `Flare NOx maximum hourly` | `nitrogen_oxides` |
| `Flare annual average NOx emission rate` | `nitrogen_oxides` |
| `Flare exhaust gas exit velocity` | `other` |
| `Flare exit velocity` | `event_status` |
| `Flare flow` | `operational_capacity` |
| `Flare flow during Run #2 per Mark D` | `operational_capacity` |
| `Flare flowrate at 10:27 AM` | `operational_capacity` |
| `Flare flowrate at 12:00 PM` | `operational_capacity` |
| `Flare inlet gas average methane content` | `methane_secondary` |
| `Flare inlet gas net heating value` | `other` |
| `Flare maximum hourly NOx emission rate` | `nitrogen_oxides` |
| `Flare minimum operating temperature` | `other` |
| `Flare minimum retention time` | `other` |
| `Flooded area at its wettest extent; stormwater pond adjacent to compost leachate pond` | `other` |
| `Flow (50050) - Average Daily` | `flow_wastewater` |
| `Flow (50050) - Maximum Daily` | `flow_wastewater` |
| `Flow (Average)` | `flow_wastewater` |
| `Flow (Maximum)` | `flow_wastewater` |
| `Flow - Daily Average` | `flow_wastewater` |
| `Flow - Daily Maximum` | `flow_wastewater` |
| `Flow - daily average` | `flow_wastewater` |
| `Flow - daily maximum` | `flow_wastewater` |
| `Flow A reading at 12:45 PM` | `operational_capacity` |
| `Flow B reading at 12:45 PM` | `operational_capacity` |
| `Flow average` | `flow_wastewater` |
| `Flow average, Final Effluent` | `flow_wastewater` |
| `Flow maximum` | `flow_wastewater` |
| `Flow maximum, Final Effluent` | `flow_wastewater` |
| `Flow monthly average` | `flow_wastewater` |
| `Flow rate` | `flow_wastewater` |
| `Flow rate at 11:37 AM Run 1` | `flow_wastewater` |
| `Flow rate during Run 3` | `flow_wastewater` |
| `Flow, average` | `flow_wastewater` |
| `Flow, average daily` | `flow_wastewater` |
| `Flow, maximum` | `flow_wastewater` |
| `Flow, maximum daily` | `flow_wastewater` |
| `Flow; maximum daily` | `flow_wastewater` |
| `Flow; monthly average` | `flow_wastewater` |
| `Fluoranthene` | `pahs` |
| `Forcemain pressure upon pump replacement` | `pressure_vacuum` |
| `Formaldehyde (HCOH) Run #2, EGT1` | `nmoc_voc` |
| `Formaldehyde HAP actual emissions <1` | `nmoc_voc` |
| `Formaldehyde HAP potential emissions >10` | `nmoc_voc` |
| `Fortistar gas plant south side of railroad tracks` | `pressure_vacuum` |
| `Fortistar turbine plant gross output capacity` | `operational_capacity` |
| `Freshwater Aquatic Value (FAV) for Vitastim Nitrifiers` | `other` |
| `From crack in ground 100 feet north of WW 315; methane` | `methane` |
| `From small crack in ground; methane` | `methane` |
| `FtS 6:2 (Fluorotelomer sulfonate)` | `pfas` |
| `FtS 6:2 (Fluorotelomer sulfonate) - Influent Pond` | `pfas` |
| `FtS 6:2 (Fluorotelomer sulfonic acid 6:2) in sample P3-6 (W)` | `pfas` |
| `FtS 6:2 (other PFAS), East side of pond` | `pfas` |
| `FtS 6:2 (other PFAS), West side of pond` | `pfas` |
| `FtS 6:2 in Outfall-001A effluent; detected in method blank` | `qa_sample` |
| `FtS 6:2 in sample Outfall-CR (W)` | `pfas` |
| `FtS 6:2 in sample P3-15 (W)` | `pfas` |
| `FtS 8:2 (other PFAS), East side of pond` | `pfas` |
| `FtS 8:2 (other PFAS), West side of pond` | `pfas` |
| `GT4 operating power during testing` | `operational_capacity` |
| `GT4 rated capacity` | `operational_capacity` |
| `Gas Turbine No. 1 landfill gas flow` | `operational_capacity` |
| `Gas Turbine No. 3 landfill gas flow` | `operational_capacity` |
| `Gas Turbine No. 4 landfill gas flow` | `operational_capacity` |
| `Gas collection efficiency - LANDGEM modeled generation normalized basis` | `operational_capacity` |
| `Gas extraction wells replaced in December 2015` | `well_operational` |
| `Gas extraction wells scheduled for replacement starting February 5, 2016` | `well_operational` |
| `Gas flow rate from cassion well #429 (June data)` | `operational_capacity` |
| `Gas flow to plant on day of inspection` | `operational_capacity` |
| `Gas plant vacuum setting` | `pressure_vacuum` |
| `Gas shortfall during AHE shutdown` | `operational_capacity` |
| `Gas-to-energy facility capacity` | `operational_capacity` |
| `Groundwater cleanup average discharge` | `flow_wastewater` |
| `Groundwater cleanup discharge average flow rate (2024 calendar year)` | `flow_wastewater` |
| `Groundwater cleanup discharge flow rate` | `flow_wastewater` |
| `Groundwater recovery sumps/wells average inflow` | `flow_wastewater` |
| `Groundwater recovery well average flow (water supply)` | `flow_wastewater` |
| `Groundwater recovery wells/sumps average flow` | `flow_wastewater` |
| `Groundwater recovery/gradient control well flow rate` | `flow_wastewater` |
| `H2S (TRS equivalent) concentration limit in landfill gas` | `hydrogen_sulfide` |
| `H2S (TRS equivalent) concentration threshold for elevated monitoring` | `trs` |
| `H2S (TRS equivalent) permit limit` | `trs` |
| `H2S (hydrogen sulfide)` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) - Turbine No. 4 Run 1` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) - Turbine No. 4 Run 2` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) - Turbine No. 4 Run 3` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) - peak reading at northern methane hot spot` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) - typical readings` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) action level at perimeter` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) action-level alarm threshold for perimeter monitors` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) at STS outlet` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) concentration maximum standard for Interim LFG Treatment Period` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) in landfill gas` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) in landfill gas, Turbine No. 4, Run 1` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) in landfill gas, Turbine No. 4, Run 2` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) in landfill gas, Turbine No. 4, Run 3` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) maximum detection range, mostly in 300s` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) minimum detection range` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) near compost entrance on 6-Mile Road` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) sampling result` | `hydrogen_sulfide` |
| `H2S (hydrogen sulfide) stack-tested via Draeger tube and bag sampled method` | `hydrogen_sulfide` |
| `H2S - Horizontal Collector in waste` | `hydrogen_sulfide` |
| `H2S - Horizontal Surface Collector` | `hydrogen_sulfide` |
| `H2S - Leach Sump with Gas Extraction; range 2000-5000 ppm` | `hydrogen_sulfide` |
| `H2S - Leachate Cleanout` | `hydrogen_sulfide` |
| `H2S - Miscellaneous` | `hydrogen_sulfide` |
| `H2S - Vertical` | `hydrogen_sulfide` |
| `H2S - Vertical with Remote` | `hydrogen_sulfide` |
| `H2S - Vertical; range 0-2000 ppm` | `hydrogen_sulfide` |
| `H2S - Vertical; range 2000-5000 ppm` | `hydrogen_sulfide` |
| `H2S - Vertical; range 20000-25000 ppm; pinched at 50 feet below surface` | `hydrogen_sulfide` |
| `H2S / TRS outlet concentration limit for STS and monitoring threshold` | `trs` |
| `H2S 2020 annual concentration` | `hydrogen_sulfide` |
| `H2S Action Level for wells` | `hydrogen_sulfide` |
| `H2S CAIRPOL all valves under 20 ppb` | `hydrogen_sulfide` |
| `H2S Draeger Tube sampling limit (allows monthly sampling if 80% of 408 ppm)` | `hydrogen_sulfide` |
| `H2S Draeger tube Test 1` | `hydrogen_sulfide` |
| `H2S Draeger tube Test 2` | `hydrogen_sulfide` |
| `H2S Draeger tube Test 3` | `hydrogen_sulfide` |
| `H2S Draeger tube maximum range Jan 4–Mar 12` | `hydrogen_sulfide` |
| `H2S Draeger tube minimum range Jan 4–Mar 12` | `hydrogen_sulfide` |
| `H2S Draeger tube range across three runs (high end)` | `hydrogen_sulfide` |
| `H2S Draeger tube range across three runs (low end)` | `hydrogen_sulfide` |
| `H2S Draeger tube results of treated landfill gas to plant` | `hydrogen_sulfide` |
| `H2S Draeger tube sample, Run 1` | `hydrogen_sulfide` |
| `H2S Draeger tube sample, collected at 10:35 AM during Run #1` | `hydrogen_sulfide` |
| `H2S Draeger tube, Run 1, first reading` | `hydrogen_sulfide` |
| `H2S Draeger tube, Run 1, second reading` | `hydrogen_sulfide` |
| `H2S Draeger tube, Run 2, first reading` | `hydrogen_sulfide` |
| `H2S Draeger tube, Run 2, second reading` | `hydrogen_sulfide` |
| `H2S Draeger tube, Run 3, first reading` | `hydrogen_sulfide` |
| `H2S Draeger tube, Run 3, second reading` | `hydrogen_sulfide` |
| `H2S Stain Tube sample result (EGT#3 Thursday)` | `hydrogen_sulfide` |
| `H2S Utility Flare compliant` | `hydrogen_sulfide` |
| `H2S above West haul road in small vents` | `hydrogen_sulfide` |
| `H2S action level alarm threshold` | `hydrogen_sulfide` |
| `H2S action level alarm threshold for perimeter monitor` | `hydrogen_sulfide` |
| `H2S action level alarm threshold for perimeter monitors` | `hydrogen_sulfide` |
| `H2S action level alarm threshold per facility procedure` | `hydrogen_sulfide` |
| `H2S action-level alarm threshold for perimeter monitors` | `hydrogen_sulfide` |
| `H2S at Compressor Vent #2 (113 ppb)` | `hydrogen_sulfide` |
| `H2S at Sewer vent #1, treatment building roof` | `hydrogen_sulfide` |
| `H2S at Sewer vent #2 (83 ppb)` | `hydrogen_sulfide` |
| `H2S at condensate sump passive vent, exceeded detection limit, from prior 10-8-21 survey` | `hydrogen_sulfide` |
| `H2S at turbine building roof, from prior 10-8-21 survey` | `hydrogen_sulfide` |
| `H2S background high end` | `hydrogen_sulfide` |
| `H2S background low end` | `hydrogen_sulfide` |
| `H2S concentration` | `hydrogen_sulfide` |
| `H2S concentration - highest reading in report` | `hydrogen_sulfide` |
| `H2S concentration at RNG plant (average landfill gas value)` | `hydrogen_sulfide` |
| `H2S concentration in leachate riser` | `hydrogen_sulfide` |
| `H2S concentration in trench` | `hydrogen_sulfide` |
| `H2S detected at waist level on treatment building roof` | `hydrogen_sulfide` |
| `H2S detected by inspector meter at TS-01` | `hydrogen_sulfide` |
| `H2S downwind of frac tanks (highest reading)` | `hydrogen_sulfide` |
| `H2S exceeded detection limit of Jerome meter; actual concentration >50 ppm from same vent` | `hydrogen_sulfide` |
| `H2S from Turbine 2 cooling vent; maximum from turbine building roof` | `hydrogen_sulfide` |
| `H2S grab sampling weekly #3` | `hydrogen_sulfide` |
| `H2S grab sampling weekly #4` | `hydrogen_sulfide` |
| `H2S hydrogen sulfide content in landfill gas (2019)` | `hydrogen_sulfide` |
| `H2S in fuel (average of Draeger tube readings: 420–460 ppmv)` | `hydrogen_sulfide` |
| `H2S in landfill gas` | `hydrogen_sulfide` |
| `H2S in landfill wells, range reported` | `hydrogen_sulfide` |
| `H2S in leachate-related well` | `hydrogen_sulfide` |
| `H2S in stack test` | `hydrogen_sulfide` |
| `H2S in treated landfill gas (Tedlar bag lab sample)` | `hydrogen_sulfide` |
| `H2S inlet typical reading, Draeger tube sampling` | `hydrogen_sulfide` |
| `H2S inside TS building at change area (47 ppb)` | `hydrogen_sulfide` |
| `H2S limit at Utility Flare (exceeded)` | `hydrogen_sulfide` |
| `H2S lowest detection (7 ppb)` | `hydrogen_sulfide` |
| `H2S maximum detection limit` | `hydrogen_sulfide` |
| `H2S near TS-01` | `hydrogen_sulfide` |
| `H2S normal high baseline (Draeger-tube determined)` | `hydrogen_sulfide` |
| `H2S odor detection threshold` | `hydrogen_sulfide` |
| `H2S outlet concentration limit for sulfur treatment system` | `hydrogen_sulfide` |
| `H2S outlet, Draeger tube sampling` | `hydrogen_sulfide` |
| `H2S perimeter monitor MS-1` | `hydrogen_sulfide` |
| `H2S perimeter walk reading via Cairpol monitor, 3.26 km route` | `hydrogen_sulfide` |
| `H2S reading at large ground holes could not be obtained; sensor saturated by very high levels` | `hydrogen_sulfide` |
| `H2S reading near condensate tank on downwind side` | `hydrogen_sulfide` |
| `H2S readings post-Sulfur Treatment System operation` | `hydrogen_sulfide` |
| `H2S revised PTI limit` | `hydrogen_sulfide` |
| `H2S sampled in stack test (8 samples ranged 350-370 ppm); Tedlar Bag 400 ppm; TRS 434 ppm` | `hydrogen_sulfide` |
| `H2S sampling limit; exceeded multiple times requiring weekly sampling` | `hydrogen_sulfide` |
| `H2S scattered elevated areas near ground` | `hydrogen_sulfide` |
| `H2S threshold for concern; actual measured exceedances indicated but specific values not stated in available text` | `hydrogen_sulfide` |
| `H2S threshold noted in aereal image legend` | `hydrogen_sulfide` |
| `H2S threshold reference; elevated levels found in NW corner above this level` | `hydrogen_sulfide` |
| `H2S via gas chromatography (continuous measurement)` | `hydrogen_sulfide` |
| `H2S, Horizontal Collector (in waste) East` | `hydrogen_sulfide` |
| `H2S, Horizontal Collector (in waste) West` | `hydrogen_sulfide` |
| `H2S, Horizontal/Surface Collector Under West Temp Cap` | `hydrogen_sulfide` |
| `H2S, Leach Sump w/ Gas Ext, range 2000-5000 ppm, mid-value used` | `hydrogen_sulfide` |
| `H2S, Leachate Cleanouts West` | `hydrogen_sulfide` |
| `H2S, Misc. West` | `hydrogen_sulfide` |
| `H2S, Vertical East` | `hydrogen_sulfide` |
| `H2S, Vertical West` | `hydrogen_sulfide` |
| `H2S, Vertical West w/Remote` | `hydrogen_sulfide` |
| `H2S, Vertical West, range 0-2000 ppm, mid-value used` | `hydrogen_sulfide` |
| `H2S, Vertical West, range 2000-5000 ppm, mid-value used` | `hydrogen_sulfide` |
| `H2S, Vertical West, range 20000-25000 ppm, mid-value used, well pinched at 50 ft depth` | `hydrogen_sulfide` |
| `H2S/TRS concentration threshold for escalated monitoring` | `hydrogen_sulfide` |
| `H2S/TRS maximum ceiling concentration in landfill gas` | `trs` |
| `H2S/TRS threshold for increased monitoring frequency` | `trs` |
| `H2S/Total Reduced Sulfur (TRS) concentration limit in landfill gas` | `trs` |
| `HCI permit limit` | `hydrogen_chloride` |
| `HCI, three-test average` | `hydrogen_chloride` |
| `HCl 12-month rolling basis limit` | `hydrogen_chloride` |
| `HCl emission limit` | `hydrogen_chloride` |
| `HCl emission limit from ROP0000224 v2.2` | `hydrogen_chloride` |
| `HCl emission limit, PTI No. 179-13` | `hydrogen_chloride` |
| `HCl emission rate` | `hydrogen_chloride` |
| `HCl emission rate, McGill flare inlet` | `hydrogen_chloride` |
| `HCl, EGT Turbine #1, Duct Burner OFF` | `hydrogen_chloride` |
| `HCl, EGT Turbine #1, Duct Burner OFF, Run average` | `hydrogen_chloride` |
| `HCl, EGT Turbine #1, Duct Burner ON` | `hydrogen_chloride` |
| `HCl, EGT Turbine #1, Duct Burner ON, Run average` | `hydrogen_chloride` |
| `HCl, EGT Turbine #3, Duct Burner OFF` | `hydrogen_chloride` |
| `HCl, EGT Turbine #3, Duct Burner OFF, Run average` | `hydrogen_chloride` |
| `HCl, EGT Turbine #3, Duct Burner ON` | `hydrogen_chloride` |
| `HCl, EGT Turbine #3, Duct Burner ON, Run average` | `hydrogen_chloride` |
| `HCl, EUTURBINE/DB1` | `hydrogen_chloride` |
| `HCl, EUTURBINE/DB2` | `hydrogen_chloride` |
| `HCl, EUTURBINE/DB3` | `hydrogen_chloride` |
| `HCl, EUTURBINE4` | `hydrogen_chloride` |
| `HCl, Typhoon Turbine Unit 2` | `hydrogen_chloride` |
| `Hardness as CaCO3 in Outfall 001A composite wastewater` | `hardness` |
| `Hardness as CaCO3, Outfall 001A` | `hardness` |
| `Hardness as CaCO3, Outfall 001A Composite` | `hardness` |
| `Hardness, Calcium as CaCO3` | `hardness` |
| `Hardness, Magnesium as CaCO3` | `hardness` |
| `Hardness, Total as CaCO3` | `hardness` |
| `Hexane at 3% O2, Typhoon Turbine Unit 2` | `btex_chlorinated_voc` |
| `Hexane, Typhoon Turbine Unit 2` | `btex_chlorinated_voc` |
| `High end daily well pumping volume` | `flow_wastewater` |
| `High end of average gas flow collected (Aug 2025)` | `operational_capacity` |
| `High methane concentration referenced in multiple wells with high liquid levels` | `methane_secondary` |
| `High methane readings reported but no specific value provided in document excerpt` | `methane` |
| `Highest LF gas flow rate recorded during inspection period (July 2023–Jan 2024)` | `operational_capacity` |
| `Highest drone SEM hit in January 2023, up the hill from cell 6` | `surface_emissions` |
| `Hydrochloric acid limit, FGENCLOSEDFLARES-S2` | `hydrogen_chloride` |
| `Hydrogen (H2)` | `hydrogen_gas` |
| `Hydrogen (H2) Drager tube reading` | `hydrogen_gas` |
| `Hydrogen (H₂) reading; scale 0.2–2.0%` | `hydrogen_gas` |
| `Hydrogen Chloride HAP actual emissions <1` | `hydrogen_chloride` |
| `Hydrogen Chloride HAP potential emissions` | `hydrogen_chloride` |
| `Hydrogen Drager tube June 2023` | `hydrogen_gas` |
| `Hydrogen Drager tube June 2023; became WOI 6/13/2023` | `hydrogen_gas` |
| `Hydrogen Drager tube May 2023` | `hydrogen_gas` |
| `Hydrogen Sulfide (H2S) via Draeger Tube sample from LFG ductwork` | `hydrogen_sulfide` |
| `Hydrogen chloride emission limit` | `hydrogen_chloride` |
| `Hydrogen chloride emission limit (12-month rolling)` | `hydrogen_chloride` |
| `Hydrogen chloride hourly limit for EUTURBINE4` | `hydrogen_chloride` |
| `Hydrogen chloride hourly limit for FGTURBINES` | `hydrogen_chloride` |
| `Hydrogen content in landfill gas` | `hydrogen_gas` |
| `Hydrogen detected in subsidence area` | `hydrogen_gas` |
| `Hydrogen peroxide solution concentration pumped into Super Sump for odor control` | `event_status` |
| `Hydrogen reading` | `hydrogen_gas` |
| `Hydrogen sulfide` | `hydrogen_sulfide` |
| `Hydrogen sulfide (H2S) action level alarm threshold for perimeter monitors` | `hydrogen_sulfide` |
| `Hydrogen sulfide (H2S) action-level alarm threshold for perimeter monitors` | `hydrogen_sulfide` |
| `Hydrogen sulfide (H2S) at passive vent from condensate sump; stated as greater than 50 ppm` | `hydrogen_sulfide` |
| `Hydrogen sulfide (H2S) in landfill gas sample from receiver/mixing container` | `hydrogen_sulfide` |
| `Hydrogen sulfide average of last two samples; reported by AHL` | `hydrogen_sulfide` |
| `Hydrogen sulfide detected at AQD 20, flap over liner west end of Cell 6` | `hydrogen_sulfide` |
| `Hydrogen sulfide in landfill gas well; highest reading recorded` | `hydrogen_sulfide` |
| `Hydrogen sulfide inlet reading via Draeger tubes` | `hydrogen_sulfide` |
| `Hydrogen sulfide, exceeds 10,000 ppm threshold` | `hydrogen_sulfide` |
| `Hydrogen sulfide, leach sump with gas extraction` | `hydrogen_sulfide` |
| `Hydrogen sulfide: first sample in 6-month collection (Silonite Summa Canister lab analysis)` | `hydrogen_sulfide` |
| `Hydrogen sulfide: second sample in 6-month collection` | `hydrogen_sulfide` |
| `Hydrogen sulfide: third sample in 6-month collection` | `hydrogen_sulfide` |
| `H₂S (hydrogen sulfide), Draeger tube test, stack test` | `hydrogen_sulfide` |
| `H₂S concentration at RNG plant (average landfill gas)` | `hydrogen_sulfide` |
| `H₂S content in well gas (Spring 2025)` | `hydrogen_sulfide` |
| `H₂S in cleaned landfill gas, Draeger sampler, Run No. 1` | `hydrogen_sulfide` |
| `H₂S in raw landfill gas measured by Draeger Tube method` | `hydrogen_sulfide` |
| `H₂S in treated LFG fuel, laboratory canister sample` | `hydrogen_sulfide` |
| `H₂S level at RNG plant STS area` | `hydrogen_sulfide` |
| `H₂S lower range reported` | `hydrogen_sulfide` |
| `H₂S upper range reported in recent times` | `hydrogen_sulfide` |
| `H₂S/TRS concentration threshold triggering weekly monitoring` | `trs` |
| `In ditch next to haul road east of WW 278 and adjacent to leachate outbreak; methane` | `methane` |
| `Increase in gas collection rate since beginning of January 2016` | `other` |
| `Initially pumped from east pond to stop overflow` | `flow_wastewater` |
| `Inside enclosure surrounding L4B; methane` | `methane` |
| `Interim blower from Lone Star received; expected operational by Feb 19` | `operational_capacity` |
| `Interim blower from inter-company landfill; expected operational by Feb 19` | `operational_capacity` |
| `Interim cover slope grade (equals 25 percent)` | `other` |
| `Just west of WW 267 from bare ground; methane` | `methane` |
| `Kinetico treatment system design capacity` | `operational_capacity` |
| `LC50 (lethal concentration) for fathead minnow, calculated from highest dose tested` | `other` |
| `LEL gas probes continue readings exceeding 1000 ppm; hydrogen/nitrogen data` | `hydrogen_gas` |
| `LFG Gas flow` | `operational_capacity` |
| `LFG Gas flow (hourly)` | `operational_capacity` |
| `LFG Treatment Standard for Sulfur Treatment System` | `trs` |
| `LFG Treatment Standard for Sulfur Treatment System when operational` | `trs` |
| `LFG Treatment Standard met when STS operational` | `trs` |
| `LFG Treatment Standard under Consent Decree First Amendment` | `trs` |
| `LFG methane content, Draeger tube sample, Run 1` | `methane` |
| `LFG methane content, Draeger tube sample, Run 2` | `methane` |
| `LFG methane content, Draeger tube sample, Run 3` | `methane` |
| `LFG sour/vinegar odor, 6 Mile Rd near scale house, 8:40–8:50 PM` | `wind_odor` |
| `LFG throughput limit (Condition II.1), 12-month rolling period` | `operational_capacity` |
| `Lab H2S resample 1` | `hydrogen_sulfide` |
| `Lab H2S resample 2` | `hydrogen_sulfide` |
| `Lab H2S result sample 1` | `hydrogen_sulfide` |
| `Lab H2S result sample 2` | `hydrogen_sulfide` |
| `Lab TRS resample 1` | `trs` |
| `Lab TRS resample 2` | `trs` |
| `Lab TRS result sample 1` | `trs` |
| `Lab TRS result sample 2` | `trs` |
| `LandGEM model calculated generation rate, suggesting significant fugitive emissions` | `operational_capacity` |
| `LandGEM model calculated landfill gas generation rate for 2025` | `operational_capacity` |
| `Landfill gas collection rate (high end) at gas plant` | `operational_capacity` |
| `Landfill gas collection rate (low end) at gas plant` | `operational_capacity` |
| `Landfill gas collection rate from Fortistar site inspection` | `operational_capacity` |
| `Landfill gas collection rate from prior inspection` | `operational_capacity` |
| `Landfill gas exceedance near settled caisson well` | `methane` |
| `Landfill gas generation rate (approximate)` | `operational_capacity` |
| `Landfill gas generation rate at start of 2021` | `operational_capacity` |
| `Landfill gas reading from Sniffer Drone survey; well/location not clarified in this exchange` | `methane` |
| `Landfill gas temperature at utility flare inlet` | `temperature_secondary` |
| `Landfill gas vacuum pressure` | `pressure_vacuum` |
| `Landfill suction pressure during inspection (normal ~70 inches H₂O); reduced due to condensate backup` | `pressure_vacuum` |
| `Landfill vacuum reading` | `pressure_vacuum` |
| `Landfill vacuum reading at 10:32 AM` | `pressure_vacuum` |
| `Leachate depth in 145-foot well sounding` | `other` |
| `Leachate depth in 150-foot well sounding` | `pressure_vacuum` |
| `Leachate depth in 150-foot well sounding; excess temperature exceedance` | `other` |
| `Leachate depth on landfill liner for Cell 4` | `pressure_vacuum` |
| `Leachate level gauge reading in the Super Sump` | `pressure_vacuum` |
| `Leachate water manifest 388729` | `flow_wastewater` |
| `Leachate water manifest 388730` | `flow_wastewater` |
| `Leachate water manifest 388731` | `flow_wastewater` |
| `Lead, sample SED-04` | `lead` |
| `Lead-Total, ONYX-COMPOST sample` | `lead` |
| `Length of 6-inch perforated horizontal gas collection pipe installed September 15, 2023` | `other` |
| `Length of aeration ditch` | `other` |
| `Length of elevated methane band observed near Monitors 4 and 5 during inspection` | `event_status` |
| `Loss of vacuum (positive pressure) event` | `event_status` |
| `Low end daily well pumping volume` | `flow_wastewater` |
| `Low end of average gas flow collected (Aug 2025)` | `operational_capacity` |
| `MSW and C&D waste collection rate` | `other` |
| `MSW/landfill gas odor level on 6 Mile Road (Level 3 is highest/strongest)` | `wind_odor` |
| `Magnesium in Outfall 001A composite wastewater` | `major_ions` |
| `Magnesium, Outfall 001A` | `major_ions` |
| `Magnesium, Outfall 001A Composite` | `major_ions` |
| `Magnesium, Sample AH` | `major_ions` |
| `Magnesium, Sample AH-FC` | `major_ions` |
| `Magnesium, Sample AH-GC` | `major_ions` |
| `Magnesium, Total` | `major_ions` |
| `Material limit - landfill gas for FGPROJECT` | `operational_capacity` |
| `Maximum Design Flow Rate and 5-year authorization request` | `flow_wastewater` |
| `Maximum H₂S concentration in landfill gas requested by AHE in PSD PTI application Draft #53-18` | `hydrogen_sulfide` |
| `Maximum SO2 emissions allowed under the STS Option Compliance Pathway (not selected)` | `sulfur_dioxide` |
| `Maximum allowable exit velocity limit` | `other` |
| `Maximum allowable exit velocity per 40 CFR 60.18(c)(4)(i)` | `event_status` |
| `Maximum authorized daily discharge flow for groundwater cleanup` | `flow_wastewater` |
| `Maximum authorized daily discharge flow rate from outfall 001` | `flow_wastewater` |
| `Maximum authorized daily discharge flow requested in reissuance application` | `flow_wastewater` |
| `Maximum authorized daily discharge for continuous discharger` | `flow_wastewater` |
| `Maximum authorized discharge flow from Monitoring Point 001A through Outfall 001` | `flow_wastewater` |
| `Maximum authorized discharge of treated groundwater` | `flow_wastewater` |
| `Maximum authorized discharge of treated groundwater from Monitoring Point 001A` | `flow_wastewater` |
| `Maximum authorized groundwater discharge flow rate from Monitoring Point 001A` | `flow_wastewater` |
| `Maximum average oxygen content threshold for LFG treatment requirement` | `oxygen` |
| `Maximum control capacity with energy plant and temp flare down due to reduced blower capacity` | `operational_capacity` |
| `Maximum design flow rate and requested authorization for discharge from outfall 004` | `flow_wastewater` |
| `Maximum design flow rate and requested discharge authorization for outfall 003` | `flow_wastewater` |
| `Maximum design flow rate for Outfall 001` | `flow_wastewater` |
| `Maximum design flow rate for outfall` | `flow_wastewater` |
| `Maximum design flow rate for outfall 001` | `flow_wastewater` |
| `Maximum design flow, outfall 001 to unnamed tributary of Johnson Creek` | `flow_wastewater` |
| `Maximum design flowrate for Utility Flare` | `operational_capacity` |
| `Maximum discharge flow authorized from Monitoring Point 001A` | `flow_wastewater` |
| `Maximum expected raw flow capacity at gas plant` | `operational_capacity` |
| `Maximum monthly average TSS loading April 2015` | `tss` |
| `Maximum permitted velocity (V_max) per 40 CFR 60.18(c)(4)(iii)` | `other` |
| `Maximum sulfur content in fuel for turbines` | `other` |
| `Maximum sulfur content in landfill gas permitted to burn in flares` | `trs` |
| `Maximum visible emissions in 2-hour period per 40 CFR 60.18(c)(1)` | `surface_emissions` |
| `McGill blower test flow` | `operational_capacity` |
| `Mercury (Hg) duplicate sample DUP-01 (Lab ID 40306006002)` | `qa_sample` |
| `Mercury (Hg) in Outfall 001A water sample (Lab ID 40306006001)` | `mercury` |
| `Mercury (Hg) influent concentration measured in treatment building with GC-1 and GC-2 running prior to treatment` | `mercury` |
| `Mercury (Low Level) in Discharge 001A water sample` | `mercury` |
| `Mercury LCA (Load Capacity Analysis) limit` | `mercury` |
| `Mercury PMP twelve-point rolling average limit` | `mercury` |
| `Mercury Total, Outfall 001A Grab` | `mercury` |
| `Mercury concentration in compost pond water sample` | `mercury` |
| `Mercury effluent 12-month rolling average limit` | `mercury` |
| `Mercury effluent concentration limit under NPDES Permit MI0045713` | `mercury` |
| `Mercury in Compost Pond` | `mercury` |
| `Mercury in DISCHARGE 001A - DUP - GRAB duplicate sample` | `qa_sample` |
| `Mercury in DISCHARGE 001A - DUPLICATE water sample` | `mercury` |
| `Mercury in DISCHARGE 001A - GRAB sample` | `mercury` |
| `Mercury in DISCHARGE 001A - GRAB water sample` | `mercury` |
| `Mercury in FIELD BLANK sample - non-detect` | `qa_sample` |
| `Mercury in compost pond water sample` | `mercury` |
| `Mercury in duplicate sample` | `mercury` |
| `Mercury influent limit under approved PMP` | `mercury` |
| `Mercury influent sample` | `mercury` |
| `Mercury influent sample GC-1, below detection limit` | `mercury` |
| `Mercury influent sample GC-1/GC-2` | `mercury` |
| `Mercury influent, collected January 2012 prior to treatment` | `mercury` |
| `Mercury quantification level (EPA Method 245.1)` | `mercury` |
| `Mercury threshold for implementing control measures` | `mercury` |
| `Mercury water quality standard` | `mercury` |
| `Mercury water quality-based effluent limit (variance level)` | `mercury` |
| `Mercury – highest measured concentration in final effluent` | `mercury` |
| `Mercury – lowest measured concentration in final effluent` | `mercury` |
| `Mercury, NPDES pond discharge effluent` | `mercury` |
| `Mercury, Outfall 001A Grab` | `mercury` |
| `Mercury, wastewater treatment system influent` | `mercury` |
| `Methane (CH4)` | `methane` |
| `Methane (CH4) - intermittent alarms greater than this value` | `methane` |
| `Methane (CH4) action level exceedance threshold at perimeter monitors MS-2, MS-3, MS-7` | `surface_emissions` |
| `Methane (CH4) adjusted` | `methane_secondary` |
| `Methane (CH4) alarm threshold; actual readings stated as 'greater than 40 ppm' but specific values not provided` | `surface_emissions` |
| `Methane (CH4) concentration; SEM exceedance context` | `methane_secondary` |
| `Methane (CH4) content, 3Q 2022` | `methane_secondary` |
| `Methane (CH4) content, 3Q 2022, WOI well` | `methane_secondary` |
| `Methane (CH4) content, 4Q 2022` | `methane_secondary` |
| `Methane (CH4) measured with SEM5000 near flag at Napier/Six Mile Road; elevated readings from compost entrance to flag` | `surface_emissions` |
| `Methane (CH4) reading during Run #2 at inlet to Gas Compressors` | `methane` |
| `Methane (CH₄) concentration` | `methane` |
| `Methane (CH₄) concentration; indicates air intrusion` | `methane_secondary` |
| `Methane action level (15-minute average) per Consent Judgment 2020-0593-CE` | `methane` |
| `Methane action level at perimeter monitoring stations` | `methane` |
| `Methane action level per Consent Judgment 2020-0593-CE over 15-minute average; regular perimeter exceedances` | `methane` |
| `Methane action level per Consent Judgment No. 2020-0593-CE (15-min average)` | `methane` |
| `Methane action-level alarm threshold for perimeter monitors` | `methane` |
| `Methane and hydrogen sulfide detected via vegetation kill event; specific ppm not quantified in available text` | `event_status` |
| `Methane at AQD 1, Cell 6B liner seam` | `methane` |
| `Methane at AQD 12, Cell 6B liner edge, western edge; highest reading` | `methane` |
| `Methane at AQD 22, subsidence crack near well WW-16R6` | `methane` |
| `Methane at AQD 6, buried trench gas line` | `methane` |
| `Methane at Compressor Vent #2` | `methane_secondary` |
| `Methane at Compressor Vent #3, second reading later` | `methane_secondary` |
| `Methane at Compressor Vent #3, treatment building roof` | `methane_secondary` |
| `Methane at MK-1, bare ground` | `methane` |
| `Methane at MK-17, Penetration WW-540` | `methane` |
| `Methane at MK-7, Penetration WW-572; H2S odors` | `methane` |
| `Methane at STS Vessel 301 vent, laser smart device` | `methane_secondary` |
| `Methane at Sewer vent #1 (near steps), treatment building roof` | `methane_secondary` |
| `Methane at Sewer vent #2` | `methane_secondary` |
| `Methane at Turbine 2 cooling vent, from prior 10-8-21 survey` | `methane` |
| `Methane at combined aux. compressor/sewer/refrigeration vent` | `methane_secondary` |
| `Methane at condensate sump passive vent, from prior 10-8-21 survey` | `methane` |
| `Methane at liner tear (MK-21)` | `methane` |
| `Methane at passive vent from condensate sump` | `methane` |
| `Methane at penetration well 502R (JB9)` | `methane` |
| `Methane concentration at monitoring station 4 during December odor event` | `methane` |
| `Methane concentration at monitoring station 5 during December odor event` | `methane` |
| `Methane concentration exceeded at monitoring station 4` | `methane` |
| `Methane concentration exceeded at monitoring station 5` | `methane` |
| `Methane concentration from bubbling/venting near well WW-16R6` | `methane` |
| `Methane concentration peak at monitoring station 4` | `methane` |
| `Methane concentration peak at monitoring station 5` | `methane` |
| `Methane concentration peak during December odor event` | `methane` |
| `Methane concentration, EURNGPLANT` | `methane_secondary` |
| `Methane concentration, perimeter monitoring` | `methane` |
| `Methane concentration, reported as stable at 51–52% since March 2023` | `methane_secondary` |
| `Methane content` | `methane` |
| `Methane content (CH4) in landfill gas during Feb 2023` | `methane_secondary` |
| `Methane content average` | `methane_secondary` |
| `Methane detector pegged at 10,000+ ppm` | `methane` |
| `Methane exceedance at property boundary gas probe` | `methane` |
| `Methane exceedance example from surface scan: '50 ft. West (and upslope) of EW65, 850 ppm'` | `methane` |
| `Methane exiting building via roof vents, high end` | `methane_secondary` |
| `Methane exiting building via roof vents, low end` | `methane_secondary` |
| `Methane from general ventilation exhaust vent, AHE roof (lower bound of 80-100 ppm range)` | `methane_secondary` |
| `Methane from general ventilation exhaust vent, AHE roof (upper bound of 80-100 ppm range)` | `methane_secondary` |
| `Methane from treatment building air vent` | `methane_secondary` |
| `Methane generally less than 10 ppm along entire west perimeter road` | `methane` |
| `Methane greater than 30 ppm (map color threshold)` | `methane` |
| `Methane greater than 50 ppm (map color threshold)` | `methane` |
| `Methane greater than 70 ppm (map color threshold)` | `methane` |
| `Methane hotspot at wellhead HW23/PW7, flagged for repair` | `methane` |
| `Methane hotspot near main gate` | `methane` |
| `Methane in adjacent roof vent` | `surface_emissions` |
| `Methane in subsidence area ditch (saturated, above limit)` | `methane` |
| `Methane inside TS building at change area` | `methane_secondary` |
| `Methane leak in roof vent ductwork gap, high-capacity vent near turbine building` | `surface_emissions` |
| `Methane level at well near HW39, Q3 2024` | `methane` |
| `Methane level in landfill gas during Solar Turbine #4 testing` | `methane_secondary` |
| `Methane over 100 ppm in NW corner near surface penetrations (map color threshold)` | `methane` |
| `Methane perimeter hit detected; specific concentration value not readable from photo documentation` | `methane` |
| `Methane plume lower bound, NE perimeter along Napier` | `methane` |
| `Methane plume upper bound, NE perimeter along Napier` | `methane` |
| `Methane quality at well 41, Q3 2024` | `methane` |
| `Methane quality, Q4 2024 SEM exceedance, WOI well` | `methane` |
| `Methane range 45–52% reported for site gas quality since March 2023` | `methane_secondary` |
| `Methane range July–December 2025, maximum monthly average` | `methane_secondary` |
| `Methane range July–December 2025, minimum monthly average` | `methane_secondary` |
| `Methane reading from Control Room during Run 2` | `methane` |
| `Methane reading from Control Room during Run 3` | `methane` |
| `Methane reading in landfill gas` | `methane` |
| `Methane readings in surface cracks and along north liner seam, in excess of 10,000 ppm` | `methane` |
| `Methane regulatory standard for surface emissions` | `surface_emissions` |
| `Methane regulatory standard for surface emissions monitoring` | `methane` |
| `Methane surface hits` | `surface_emissions` |
| `Methane threshold for active gas collection requirement in Cell 6A per CJ 2020-0593-CE` | `methane` |
| `Methane threshold marking red zones on survey map` | `methane` |
| `Methane threshold triggering active gas collection requirement under CJ 2020-0593-CE` | `methane` |
| `Methane threshold; document indicates measured exceedances at multiple locations` | `methane` |
| `Methane, Napier/6 Mile background, October 2020` | `methane` |
| `Methane, cyan-colored range on survey map` | `methane` |
| `Methane, downwind of active face` | `methane` |
| `Methane, lower threshold of survey grid pattern on north slope` | `methane` |
| `Methane, pink-colored range on survey map` | `methane` |
| `Methane, upper range on north slope; orange-colored on survey map` | `methane` |
| `Methane: no reading detected at abandoned well, east side hill` | `methane` |
| `Methane; threshold for red highlighting on survey map` | `methane` |
| `Michigan Human Noncancer Value (HNV) for non-drinking water — regulatory threshold` | `other` |
| `Michigan Rule 323.1057 Rule 57 HNV for PFOS in non-drinking water` | `pfas` |
| `Michigan Rule 57 HNV for PFOA in non-drinking water` | `pfas` |
| `Michigan Rule 57 Human Noncancer Value (HNV) for PFOA (non-drinking water)` | `pfas` |
| `Michigan Rule 57 Human Noncancer Value (HNV) for PFOS (non-drinking water)` | `pfas` |
| `Michigan water quality standard for PFOS` | `pfas` |
| `Michigan water quality standard for PFOS in surface waters` | `pfas` |
| `Minimum H2S odor detection threshold` | `hydrogen_sulfide` |
| `Minimum SO2 odor threshold` | `sulfur_dioxide` |
| `Minimum average methane content threshold for LFG treatment requirement` | `methane_secondary` |
| `Minimum destruction temperature for thermal oxidizer EURNGTOX` | `other` |
| `Minimum dissolved oxygen standard, coldwater protection` | `dissolved_oxygen` |
| `Minimum filtration requirement for desulfurized landfill gas treatment` | `operational_capacity` |
| `Minimum flowrate after initial startup purge` | `operational_capacity` |
| `Minimum net heating value for non-assisted flares` | `other` |
| `Minimum net heating value of landfill gas for non-assisted flares` | `combustion_efficiency` |
| `Minimum net heating value required per 40 CFR 60.18(c)(3)(ii)` | `combustion_efficiency` |
| `Minimum net heating value requirement per 40 CFR 60.18(c)(3)(ii)` | `other` |
| `Minimum recommended starting flowrate for flare` | `operational_capacity` |
| `Minimum required net heating value` | `other` |
| `Minimum required vacuum per test protocol` | `pressure_vacuum` |
| `Minimum target vacuum maintenance at all wells per Pearse-Bossick` | `pressure_vacuum` |
| `Minor methane leak at pipe flange, STS` | `methane_secondary` |
| `Monthly application dosage concentration` | `other` |
| `Monthly average TSS concentration calculated April 2015` | `tss` |
| `Monthly average TSS loading permit limit` | `tss` |
| `Monthly average TSS permit limit` | `tss` |
| `N-MeFOSAA in DAF Effluent leachate sample` | `pfas` |
| `N-MeFOSAA in DAF effluent` | `pfas` |
| `N-MeFOSAA in leachate DAF effluent` | `pfas` |
| `NEtFOSAA in stormwater sample AH-STORM WATER` | `pfas` |
| `NH3-N (ammonia) daily max, Arbor Hills, May–September (AWT)` | `ammonia_nitrogen` |
| `NH3-N 30-day average, Arbor Hills, May–September (AWT)` | `ammonia_nitrogen` |
| `NH3-N spring 30-day avg, Onyx Arbor Hills LF` | `ammonia_nitrogen` |
| `NH3-N spring daily max, Onyx Arbor Hills LF` | `ammonia_nitrogen` |
| `NH3-N summer/fall 30-day avg, Onyx Arbor Hills LF` | `ammonia_nitrogen` |
| `NH3-N summer/fall daily max, Onyx Arbor Hills LF` | `ammonia_nitrogen` |
| `NH3-N winter 30-day avg, Onyx Arbor Hills LF` | `ammonia_nitrogen` |
| `NMOC` | `nmoc_voc` |
| `NMOC (Non-Methane Organic Compounds)` | `nmoc_voc` |
| `NMOC (non-methane organic compounds), Flare 391, 3-test average` | `nmoc_voc` |
| `NMOC (non-methane organic compounds), Flare 392, 3-test average` | `nmoc_voc` |
| `NMOC allowable limit (as hexane @ 3% O2) per PTI 67-23A and 40 CFR 60.762` | `nmoc_voc` |
| `NMOC as hexane @ 3% O2, three-test average` | `nmoc_voc` |
| `NMOC at detection level, EURNGTOX` | `nmoc_voc` |
| `NMOC concentration as hexane, dry basis corrected to 3% oxygen` | `nmoc_voc` |
| `NMOC dry basis limit` | `nmoc_voc` |
| `NMOC emission limit for each enclosed flare at 3% O2` | `nmoc_voc` |
| `NMOC exhaust concentration limit, 40 CFR 60.752(b)(2)(iii)(B)` | `nmoc_voc` |
| `NMOC exhaust concentration, dry basis as hexane, corrected to 3% oxygen` | `nmoc_voc` |
| `NMOC outlet concentration compliance threshold` | `nmoc_voc` |
| `NMOC permit limit for enclosed flares` | `nmoc_voc` |
| `NMOC permitted limit under 40 CFR Part 63 Subpart AAAA` | `nmoc_voc` |
| `NMOC reduction requirement for enclosed flares` | `nmoc_voc` |
| `NO analyzer reading at 11:52 AM` | `nitrogen_oxides` |
| `NO2 analyzer reading at 11:52 AM` | `nitrogen_oxides` |
| `NO2 permitted limit, Solar Turbine GT4` | `nitrogen_oxides` |
| `NO2 permitted limit, Typhoon Turbine 1` | `nitrogen_oxides` |
| `NO2 permitted limit, Typhoon Turbine 2` | `nitrogen_oxides` |
| `NO2 permitted limit, Typhoon Turbine 3` | `nitrogen_oxides` |
| `NO2 pollutant limit, Turbine 1` | `nitrogen_oxides` |
| `NO2 pollutant limit, Turbine 2` | `nitrogen_oxides` |
| `NO2 pollutant limit, Turbine 3` | `nitrogen_oxides` |
| `NO2 pollutant limit, Turbine 4` | `nitrogen_oxides` |
| `NO2, Solar Turbine GT4 (74 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Solar Turbine GT4, Run average` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 1 (199 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 1 (204 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 1 (206 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 1 (207 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 1, Run average` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 2 (230 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 2 (234 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 2 (236 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 2 (240 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 2, Run average` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 3 (190 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 3 (194 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 3 (196 ppmvd limit), Run Avg` | `nitrogen_oxides` |
| `NO2, Typhoon Turbine 3, Run average` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 1, Run 1` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 1, Run 2` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 1, Run 3` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 1, Three-Run Average` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 2, Run 1` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 2, Run 2` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 2, Run 3` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 2, Three-Run Average` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 3, Run 1` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 3, Run 2` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 3, Run 3` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 3, Three-Run Average` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 4, Run 1` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 4, Run 2` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 4, Run 3` | `nitrogen_oxides` |
| `NO2, USEPA Method 7E, Turbine 4, Three-Run Average` | `nitrogen_oxides` |
| `NOX new allowable limit for EUOFRNG` | `nitrogen_oxides` |
| `NOX previous allowable limit for EUOFRNG` | `nitrogen_oxides` |
| `NOx 12-month limit` | `nitrogen_oxides` |
| `NOx 12-month rolling limit for EUOFRNG` | `nitrogen_oxides` |
| `NOx 12-month rolling limit for EURNGTOX` | `nitrogen_oxides` |
| `NOx 12-month rolling limit for EUTURBINE4` | `nitrogen_oxides` |
| `NOx 12-month rolling limit for FGPROJECT23` | `nitrogen_oxides` |
| `NOx 12-month rolling limit for FGPROJECT23 (all four turbines combined)` | `nitrogen_oxides` |
| `NOx 12-month rolling limit for FGTURBINES` | `nitrogen_oxides` |
| `NOx Post-run Mid gas` | `qa_sample` |
| `NOx Post-run Zero gas` | `qa_sample` |
| `NOx Pre-run Mid gas` | `qa_sample` |
| `NOx Pre-run Zero gas` | `qa_sample` |
| `NOx Run 1, EGT3` | `nitrogen_oxides` |
| `NOx Run 2, EGT3` | `nitrogen_oxides` |
| `NOx Run 3, EGT3` | `nitrogen_oxides` |
| `NOx actual emissions 2025` | `nitrogen_oxides` |
| `NOx actual emissions <1` | `nitrogen_oxides` |
| `NOx allowable emission limit alternative for EUTURBINE4, PTI 68-23A V2.0` | `nitrogen_oxides` |
| `NOx allowable emission limit for EUTURBINE4, PTI 68-23A V2.0` | `nitrogen_oxides` |
| `NOx allowable emission limit per PTI 67-23A` | `nitrogen_oxides` |
| `NOx analyzer reading at 11:52 AM, below limit` | `nitrogen_oxides` |
| `NOx annual limit for EUTURBINE4` | `nitrogen_oxides` |
| `NOx at 15% O2 for EUTURBINE4` | `nitrogen_oxides` |
| `NOx at maximum load; permit limit 0.060 lb/mmBtu` | `nitrogen_oxides` |
| `NOx at standard load; permit limit 0.060 lb/mmBtu` | `nitrogen_oxides` |
| `NOx average concentration from test run` | `nitrogen_oxides` |
| `NOx calendar year 2020 MAERS report` | `nitrogen_oxides` |
| `NOx concentration (dry basis)` | `nitrogen_oxides` |
| `NOx concentration Test 1` | `nitrogen_oxides` |
| `NOx concentration Test 2` | `nitrogen_oxides` |
| `NOx concentration Test 3` | `nitrogen_oxides` |
| `NOx concentration corrected to 15% O2 reference` | `nitrogen_oxides` |
| `NOx concentration three-test average` | `nitrogen_oxides` |
| `NOx concentration, Flare 391 (EUENCLOSEDFLARE2-S2), 3-test average` | `nitrogen_oxides` |
| `NOx concentration, Flare 392 (EUENCLOSEDFLARE1-S2), 3-test average` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 1 Set Point 1, Test 1` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 1 Set Point 1, Test 2` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 1 Set Point 1, Test 3` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 1 Set Point 2, Test 4` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 1 Set Point 2, Test 5` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 1 Set Point 2, Test 6` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 1 Set Point 3, Test 7` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 1 Set Point 3, Test 8` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 1 Set Point 3, Test 9` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 1, Test 1` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 1, Test 2` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 1, Test 3` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 2, Test 4` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 2, Test 5` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 2, Test 6` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 3, Test 7` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 3, Test 8` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 3, Test 9` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 4, Test 10` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 4, Test 11` | `nitrogen_oxides` |
| `NOx concentration, Turbine No. 2 Set Point 4, Test 12` | `nitrogen_oxides` |
| `NOx corrected to 15% oxygen` | `nitrogen_oxides` |
| `NOx corrected to 15% oxygen, Solar Taurus Gas Turbine` | `nitrogen_oxides` |
| `NOx corrected to 15% oxygen, Typhoon Turbine Unit 2` | `nitrogen_oxides` |
| `NOx emission limit` | `nitrogen_oxides` |
| `NOx emission limit (12-month rolling) for open flare` | `nitrogen_oxides` |
| `NOx emission limit (12-month rolling) for thermal oxidizer` | `nitrogen_oxides` |
| `NOx emission limit (alternative basis)` | `nitrogen_oxides` |
| `NOx emission limit (hourly) for open flare` | `nitrogen_oxides` |
| `NOx emission limit (hourly) for thermal oxidizer` | `nitrogen_oxides` |
| `NOx emission limit @ 15% O2` | `nitrogen_oxides` |
| `NOx emission limit for EU5000CFMFLARE, hourly` | `nitrogen_oxides` |
| `NOx emission limit for EUOFRNG (12-month rolling)` | `nitrogen_oxides` |
| `NOx emission limit for EUOFRNG open flare (hourly)` | `nitrogen_oxides` |
| `NOx emission limit for EURNGTOX (12-month rolling)` | `nitrogen_oxides` |
| `NOx emission limit for EURNGTOX (hourly)` | `nitrogen_oxides` |
| `NOx emission limit for FGENCLOSEDFLARES` | `nitrogen_oxides` |
| `NOx emission limit for FGENCLOSEDFLARES-S2, hourly` | `nitrogen_oxides` |
| `NOx emission limit, PTI No. 179-13` | `nitrogen_oxides` |
| `NOx emission rate` | `nitrogen_oxides` |
| `NOx emission rate AHE Turbine 3` | `nitrogen_oxides` |
| `NOx emission rate AHE Turbines 1-2` | `nitrogen_oxides` |
| `NOx emission rate from EUTURBINE4-S3` | `nitrogen_oxides` |
| `NOx emission rate permit limit` | `nitrogen_oxides` |
| `NOx emission rate, McGill flare exhaust` | `nitrogen_oxides` |
| `NOx emission rate, average of 3 runs` | `nitrogen_oxides` |
| `NOx emissions` | `nitrogen_oxides` |
| `NOx emissions Test 1` | `nitrogen_oxides` |
| `NOx emissions Test 2` | `nitrogen_oxides` |
| `NOx emissions Test 3` | `nitrogen_oxides` |
| `NOx emissions three-test average` | `nitrogen_oxides` |
| `NOx emissions, 12-month rolling average for FGPROJECT23` | `nitrogen_oxides` |
| `NOx emissions, 2007 stack test` | `nitrogen_oxides` |
| `NOx emissions, 2008 stack test` | `nitrogen_oxides` |
| `NOx emissions, Flare 391, 3-test average` | `nitrogen_oxides` |
| `NOx emissions, Flare 392, 3-test average` | `nitrogen_oxides` |
| `NOx hourly emission limit for EURNGTOX` | `nitrogen_oxides` |
| `NOx hourly limit for EUTURBINE4` | `nitrogen_oxides` |
| `NOx hourly limit for EUTURBINE4 under 40 CFR Part 60 Subpart KKKK` | `nitrogen_oxides` |
| `NOx hourly limit for FGTURBINES (normal operation with duct burner)` | `nitrogen_oxides` |
| `NOx hourly limit for open flare EUOFRNG` | `nitrogen_oxides` |
| `NOx hourly limit per permit` | `nitrogen_oxides` |
| `NOx limit for EUTURBINE4` | `nitrogen_oxides` |
| `NOx lower range during Turbine #2 stratification test` | `nitrogen_oxides` |
| `NOx mass emissions` | `nitrogen_oxides` |
| `NOx max run concentration` | `nitrogen_oxides` |
| `NOx min run concentration` | `nitrogen_oxides` |
| `NOx normal operation for FGTURBINES` | `nitrogen_oxides` |
| `NOx permit limit` | `nitrogen_oxides` |
| `NOx permit limit (RO Permit MI-ROP-N2688-2011)` | `nitrogen_oxides` |
| `NOx permit limit Turbines 1-3` | `nitrogen_oxides` |
| `NOx permit limit for Solar GT#4` | `nitrogen_oxides` |
| `NOx permit limit for enclosed flares` | `nitrogen_oxides` |
| `NOx permit limit, EUTURBINE4` | `nitrogen_oxides` |
| `NOx permit limit, FGTURBINES` | `nitrogen_oxides` |
| `NOx permit limit, combined turbines` | `nitrogen_oxides` |
| `NOx potential emissions` | `nitrogen_oxides` |
| `NOx rate limit equivalent` | `nitrogen_oxides` |
| `NOx turbine 1, October 16–19 2018 retest` | `nitrogen_oxides` |
| `NOx turbine 2, October 16–19 2018 retest` | `nitrogen_oxides` |
| `NOx turbine 3, October 16–19 2018 retest` | `nitrogen_oxides` |
| `NOx upper range during Turbine #2 stratification test` | `nitrogen_oxides` |
| `NOx, EGT Turbine #1, Duct Burner OFF` | `nitrogen_oxides` |
| `NOx, EGT Turbine #1, Duct Burner OFF, Run average` | `nitrogen_oxides` |
| `NOx, EGT Turbine #1, Duct Burner ON` | `nitrogen_oxides` |
| `NOx, EGT Turbine #1, Duct Burner ON, Run average` | `nitrogen_oxides` |
| `NOx, EGT Turbine #3, Duct Burner OFF` | `nitrogen_oxides` |
| `NOx, EGT Turbine #3, Duct Burner OFF, Run average` | `nitrogen_oxides` |
| `NOx, EGT Turbine #3, Duct Burner ON` | `nitrogen_oxides` |
| `NOx, EGT Turbine #3, Duct Burner ON, Run average` | `nitrogen_oxides` |
| `NOx, EUTURBINE/DB1` | `nitrogen_oxides` |
| `NOx, EUTURBINE/DB2` | `nitrogen_oxides` |
| `NOx, EUTURBINE/DB3` | `nitrogen_oxides` |
| `NOx, EUTURBINE4` | `nitrogen_oxides` |
| `NOx, Run 1, Solar Turbine 4` | `nitrogen_oxides` |
| `NOx, Run 2, Solar Turbine 4` | `nitrogen_oxides` |
| `NOx, Run 3, Solar Turbine 4` | `nitrogen_oxides` |
| `NOx, Solar Taurus Gas Turbine` | `nitrogen_oxides` |
| `NOx, Turbine 1 only mode, three-test average` | `nitrogen_oxides` |
| `NOx, Turbine 2 only mode, three-test average` | `nitrogen_oxides` |
| `NOx, Turbine 3 only mode, three-test average` | `nitrogen_oxides` |
| `NOx, Turbine 4 (Solar Taurus), three-test average` | `nitrogen_oxides` |
| `NOx, Turbine 4, three-test average` | `nitrogen_oxides` |
| `NOx, Typhoon Turbine Unit 2` | `nitrogen_oxides` |
| `NOx; permit limit 0.060 lb/mmBtu` | `nitrogen_oxides` |
| `NO₂ 1-hour NAAQS limit` | `nitrogen_oxides` |
| `NO₂ 1-hour combined impact (facility + background) operating scenario 1` | `nitrogen_oxides` |
| `NOₓ from Turbine 3, stack test Oct 16–19, 2018; limit 8.8 lb/hr (near limit)` | `nitrogen_oxides` |
| `NPDES discharge volume between December 19–20, 2021` | `flow_wastewater` |
| `NPDES permit limit for pH effluent` | `ph` |
| `NSPS Subpart WWW surface methane concentration standard per 40 CFR 60.753(d)` | `methane` |
| `NSPS requirement for GCCS expansion or ACT submission` | `event_status` |
| `Nameplate capacity limit for EUOPENFLARE_TEMP` | `operational_capacity` |
| `Nameplate capacity of EU5000CFMFLARE open utility flare` | `operational_capacity` |
| `Nameplate capacity of EUOPENFLARE_TEMP (temporary open flare)` | `operational_capacity` |
| `Near WW 278; methane` | `methane` |
| `Net heating value minimum for non-assisted flares` | `other` |
| `Net heating value minimum for steam-assisted or air-assisted flares` | `other` |
| `Net heating value of landfill gas being combusted` | `other` |
| `Net heating value of landfill gas combusted` | `combustion_efficiency` |
| `New 5,000 cfm flare permitted in April 2018` | `operational_capacity` |
| `New NOX emissions allowable limit` | `nitrogen_oxides` |
| `New Open Flare capacity` | `operational_capacity` |
| `New and replacement gas extraction wells installed` | `well_operational` |
| `New candlestick flare capacity (began operation November 17, 2018)` | `operational_capacity` |
| `New gas extraction wells in Cell 4 and underlying cells, scheduled completion February 19, 2016` | `well_operational` |
| `New gas extraction wells scheduled for installation starting February 5, 2016` | `well_operational` |
| `New open flare (candlestick) capacity, operational 2018-11-17` | `operational_capacity` |
| `New wellfield installed in Cell 4` | `well_operational` |
| `Nickel - measured data point` | `nickel` |
| `Nickel - recommended monthly limit` | `nickel` |
| `Nickel PEL (FCV monthly avg)` | `nickel` |
| `Nickel PEQ` | `nickel` |
| `Nickel discharge limit` | `nickel` |
| `Nickel effluent concentration` | `nickel` |
| `Nickel in Outfall 001A composite wastewater; above RDL 5.0 ug/L` | `nickel` |
| `Nickel in influent` | `nickel` |
| `Nickel in influent; PEL/FCV 120 ug/L` | `nickel` |
| `Nickel in leachate effluent` | `nickel` |
| `Nickel recommended monthly limit` | `nickel` |
| `Nickel, Outfall 001A` | `nickel` |
| `Nickel, Outfall 001A Composite` | `nickel` |
| `Nickel, Sample AH` | `nickel` |
| `Nickel, Total` | `nickel` |
| `Nitrafix concentration in 4,000,000-gallon treatment pond` | `other` |
| `Nitrafix concentration in pond (calculated from 10 gallons in 4 million gallons over 10 days)` | `other` |
| `Nitrogen Ammonia, West Pond sample` | `ammonia_nitrogen` |
| `Nitrogen Oxides (NOx)` | `nitrogen_oxides` |
| `Nitrogen Oxides (NOx) - Total stationary source emissions` | `nitrogen_oxides` |
| `Nitrogen Oxides NO2 limit for EUTURBINE4` | `nitrogen_oxides` |
| `Nitrogen Oxides NO2, Typhoon Turbine 1` | `nitrogen_oxides` |
| `Nitrogen Oxides NO2, Typhoon Turbine 2` | `nitrogen_oxides` |
| `Nitrogen Oxides NO2, Typhoon Turbine 3` | `nitrogen_oxides` |
| `Nitrogen Oxides NO2, Typhoon Turbine unit` | `nitrogen_oxides` |
| `Nitrogen ammonia, exceeded 0.4 lbs/day limit` | `ammonia_nitrogen` |
| `Nitrogen ammonia, exceeded 0.5 mg/L limit` | `ammonia_nitrogen` |
| `Nitrogen oxides (NO2 + NO) three-run average, FGENCLOSEDFLARES-S2` | `nitrogen_oxides` |
| `Nitrogen oxides (NOx) limit for EUENCLOSEDFLARE1 and EUENCLOSEDFLARE2 under 40 CFR Part 63 Subpart AAAA` | `nitrogen_oxides` |
| `Nitrogen oxides (NOx) limit for FGENCLOSEDFLARES-S2` | `nitrogen_oxides` |
| `Nitrogen oxides limit` | `nitrogen_oxides` |
| `Nitrogen oxides limit, normal operation with duct burner` | `nitrogen_oxides` |
| `Nitrogen oxides permitted limit` | `nitrogen_oxides` |
| `Nitrogen reading from H&N balance gas data` | `other` |
| `Nitrogen, Ammonia` | `ammonia_nitrogen` |
| `Nonmethane organic compounds (NMOC) limit for EUENCLOSEDFLARE1 and EUENCLOSEDFLARE2 under 40 CFR Part 63 Subpart AAAA` | `nmoc_voc` |
| `Nonmethane organic compounds (NMOC) limit for EUENCLOSEDFLARE1-S2 and EUENCLOSEDFLARE2-S2` | `nmoc_voc` |
| `Nonmethane organic compounds (NMOC), three-run average` | `nmoc_voc` |
| `North end of new 18-inch gas header line; previously ~10 inches` | `pressure_vacuum` |
| `North side of railroad tracks where East and West gas loops begin` | `pressure_vacuum` |
| `N₂ composition in landfill gas, Run No. 2` | `other` |
| `O2 Post-run Mid gas` | `qa_sample` |
| `O2 Post-run Zero gas` | `qa_sample` |
| `O2 Pre-run Hi gas` | `qa_sample` |
| `O2 Pre-run Zero gas` | `qa_sample` |
| `O2 gas composition` | `oxygen` |
| `O2 gas composition; elevated O2` | `oxygen` |
| `O2 gas composition; elevated O2 noted` | `oxygen` |
| `O2 oxygen percentage; elevated reading` | `oxygen` |
| `O2, Run 1` | `oxygen` |
| `O2, Run 2` | `oxygen` |
| `O2, Run 3` | `oxygen` |
| `Observed duration of diesel fuel use and black smoke emissions from GT1 & GT2 (10:06 to 10:31)` | `event_status` |
| `Observed visible emissions (3 min 4 sec accumulated total)` | `surface_emissions` |
| `Odor complaints for 2018` | `wind_odor` |
| `Odor intensity observed along 5 Mile Road, 50 ft length` | `wind_odor` |
| `Odor readings less than 2 dilution factor (1,072 of 10,512)` | `wind_odor` |
| `Odor readings showing zero or no odor (9,437 of 10,512)` | `wind_odor` |
| `Offsite underground gas migration east of landfill across Napier Road` | `surface_emissions` |
| `Oil and grease` | `other` |
| `On dirt road from bare ground 100 feet southwest of EW51R; methane` | `methane` |
| `Onyx Arbor Hills LF design discharge flow` | `operational_capacity` |
| `Open flare stack height` | `operational_capacity` |
| `Operating hours for significance` | `operational_capacity` |
| `Operating pressure at time of inspection` | `pressure_vacuum` |
| `Original design blower capacity target` | `operational_capacity` |
| `Outfall 001 authorized maximum flow` | `flow_wastewater` |
| `Outfall 001 final effluent flow, average` | `flow_wastewater` |
| `Outfall 001 final effluent flow, maximum range` | `flow_wastewater` |
| `Outfall 001 final effluent flow, minimum range` | `flow_wastewater` |
| `Outfall 001 final effluent pH, average` | `ph` |
| `Outfall 001 final effluent pH, maximum` | `ph` |
| `Outfall 001 final effluent pH, minimum` | `ph` |
| `Outlet NMOC concentration limit for enclosed flares (dry basis as hexane at 3% oxygen)` | `nmoc_voc` |
| `Overall available screens submerged with liquid/sediment (Spring 2025)` | `well_operational` |
| `Overall methane concentration in landfill gas` | `methane_secondary` |
| `Overall methane concentration in landfill gas (April 2026; higher than normal)` | `methane_secondary` |
| `Oxides of Nitrogen (NOx), average of 3 runs` | `nitrogen_oxides` |
| `Oxygen (O2) content in turbine exhaust` | `oxygen` |
| `Oxygen at well 41, Q3 2024` | `oxygen` |
| `Oxygen concentration in landfill gas, stable at 1.5–2% since March 2023` | `oxygen` |
| `Oxygen level at well 272R4` | `oxygen` |
| `Oxygen range July–December 2025, maximum monthly average` | `oxygen` |
| `Oxygen range July–December 2025, minimum monthly average` | `oxygen` |
| `PAH (polycyclic aromatic hydrocarbons) at highest % of annual TAC screening level` | `pahs` |
| `PAH 1-hour impact Scenario 1 combined` | `pahs` |
| `PAH 1-hour impact Scenario 2 combined` | `pahs` |
| `PAH annual impact Scenario 1 combined` | `pahs` |
| `PAH annual impact Scenario 2 combined` | `pahs` |
| `PFAS (mainly PFOS) in Creek Chad fish from Johnson Creek near Fish Hatchery Park` | `pfas` |
| `PFAS (mainly PFOS) in Creek Chub from Johnson Creek near Fish Hatchery Park` | `pfas` |
| `PFAS compound reduction by Arbor Hills West new treatment system` | `pfas` |
| `PFBA` | `pfas` |
| `PFBA (Perfluorobutanoic Acid) in Discharge 001A-Grab water sample` | `pfas` |
| `PFBA (Perfluorobutanoic Acid) in Outfall-001A Grab` | `pfas` |
| `PFBA (Perfluorobutanoic Acid) in Outfall-001A wastewater sample` | `pfas` |
| `PFBA (Perfluorobutanoic Acid) in wastewater outfall` | `pfas` |
| `PFBA (Perfluorobutanoic Acid), Outfall 001A Grab` | `pfas` |
| `PFBA (Perfluorobutanoic acid) in sample P3-6 (W)` | `pfas` |
| `PFBA (per- and polyfluoroalkyl substance) in Compost Pond` | `pfas` |
| `PFBA (perfluorobutanoic acid)` | `pfas` |
| `PFBA (perfluorobutanoic acid), Outfall 001A effluent` | `pfas` |
| `PFBA (perfluorobutanoic acid), Outfall 001A grab sample` | `pfas` |
| `PFBA (perfluorobutyric acid) in leachate DAF effluent` | `pfas` |
| `PFBA in Compost Pond YCUA-1` | `pfas` |
| `PFBA in DAF Effluent leachate sample` | `pfas` |
| `PFBA in DAF effluent` | `pfas` |
| `PFBA in Outfall 001A effluent` | `pfas` |
| `PFBA in Outfall 001A wastewater` | `pfas` |
| `PFBA in Outfall-001A effluent` | `pfas` |
| `PFBA in Outfall-001A effluent; detected in method blank` | `qa_sample` |
| `PFBA in Outfall-001A wastewater` | `pfas` |
| `PFBA in Outfall-001A wastewater sample` | `pfas` |
| `PFBA in groundwater sample AH-001A` | `pfas` |
| `PFBA in sample AH-001A` | `pfas` |
| `PFBA in sample P3-15 (W)` | `pfas` |
| `PFBA in stormwater sample AH-STORM WATER` | `pfas` |
| `PFBA in treated wastewater effluent Outfall-001A` | `pfas` |
| `PFBA in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFBA in wastewater Outfall 001A` | `pfas` |
| `PFBA in wastewater Outfall-001A` | `pfas` |
| `PFBA in wastewater outfall sample` | `pfas` |
| `PFBA, Outfall 001A` | `pfas` |
| `PFBA, Outfall 001A Grab` | `pfas` |
| `PFBA, Outfall 001A field duplicate` | `qa_sample` |
| `PFBS` | `pfas` |
| `PFBS (Perfluorobutane Sulfonic Acid) in Discharge 001A-Grab water sample` | `pfas` |
| `PFBS (Perfluorobutane Sulfonic Acid) in Outfall-001A wastewater sample` | `pfas` |
| `PFBS (Perfluorobutane Sulfonic Acid) in wastewater outfall` | `pfas` |
| `PFBS (Perfluorobutane sulfonic Acid) in Outfall-001A Grab` | `pfas` |
| `PFBS (Perfluorobutane sulfonic Acid), Outfall 001A Grab` | `pfas` |
| `PFBS (Perfluorobutane sulfonic acid)` | `pfas` |
| `PFBS (Perfluorobutane sulfonic acid) - Influent Pond` | `pfas` |
| `PFBS (Perfluorobutanesulfonic Acid)` | `pfas` |
| `PFBS (Perfluorobutanesulfonic Acid) - Arbor Hills Remediation Area sample 001A` | `pfas` |
| `PFBS (Perfluorobutanesulfonic Acid) - EGLE sample 001A` | `pfas` |
| `PFBS (Perfluorobutanesulfonic Acid), grab sample` | `pfas` |
| `PFBS (Perfluorobutanesulfonic Acid), grab sample facility` | `pfas` |
| `PFBS (Perfluorobutanesulfonic acid)` | `pfas` |
| `PFBS (Perfluorobutanesulfonic acid) - January quarterly grab` | `pfas` |
| `PFBS (Perfluorobutanesulfonic acid) in sample P3-6 (W)` | `pfas` |
| `PFBS (Perfluorobutanesulfonic acid), Final Effluent` | `pfas` |
| `PFBS (Perfluorobutanesulfonic acid), Final Effluent (1)` | `pfas` |
| `PFBS (Perfluorobutanesulfonic acid), quarterly grab sample` | `pfas` |
| `PFBS (Perfluorobutanesulfonic acid); permit requires reporting` | `pfas` |
| `PFBS (per- and polyfluoroalkyl substance) in Compost Pond` | `pfas` |
| `PFBS (perfluorobutane sulfonate) in AHE raw leachate` | `pfas` |
| `PFBS (perfluorobutane sulfonate), Outfall 001A grab sample` | `pfas` |
| `PFBS (perfluorobutane sulfonic acid)` | `pfas` |
| `PFBS (perfluorobutane sulfonic acid) in leachate DAF effluent` | `pfas` |
| `PFBS (perfluorobutane sulfonic acid), Outfall 001A effluent` | `pfas` |
| `PFBS - Effluent Pond` | `pfas` |
| `PFBS - Final Effluent (1)` | `pfas` |
| `PFBS - Outfall-CR (Johnson Drain)` | `pfas` |
| `PFBS in Compost Pond YCUA-1` | `pfas` |
| `PFBS in DAF Effluent leachate sample` | `pfas` |
| `PFBS in DAF effluent` | `pfas` |
| `PFBS in Outfall 001A effluent` | `pfas` |
| `PFBS in Outfall 001A wastewater` | `pfas` |
| `PFBS in Outfall-001A effluent` | `pfas` |
| `PFBS in Outfall-001A wastewater` | `pfas` |
| `PFBS in groundwater sample AH-001A` | `pfas` |
| `PFBS in sample AH-001A` | `pfas` |
| `PFBS in sample Outfall-CR (W)` | `pfas` |
| `PFBS in sample P3-15 (W)` | `pfas` |
| `PFBS in stormwater sample AH-STORM WATER` | `pfas` |
| `PFBS in treated wastewater effluent Outfall-001A` | `pfas` |
| `PFBS in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFBS in wastewater Outfall 001A` | `pfas` |
| `PFBS in wastewater Outfall-001A` | `pfas` |
| `PFBS in wastewater outfall sample` | `pfas` |
| `PFBS, Outfall 001A` | `pfas` |
| `PFBS, Outfall 001A Grab` | `pfas` |
| `PFBS, Outfall 001A field duplicate` | `qa_sample` |
| `PFDA (perfluorodecanoic acid) in leachate DAF effluent` | `pfas` |
| `PFDA in DAF Effluent leachate sample` | `pfas` |
| `PFDA in DAF effluent` | `pfas` |
| `PFDA in stormwater sample AH-STORM WATER` | `pfas` |
| `PFECHS (Perfluoro-4-ethylcyclohexanesulfonate) in Outfall-001A Grab` | `pfas` |
| `PFECHS (Perfluoro-4-ethylcyclohexanesulfonate) in Outfall-001A wastewater sample` | `pfas` |
| `PFECHS (Perfluoro-4-ethylcyclohexanesulfonate) in wastewater outfall` | `pfas` |
| `PFECHS (Perfluoro-4-ethylcyclohexanesulfonate), Outfall 001A Grab` | `pfas` |
| `PFECHS (perfluoro-4-ethylcyclohexanesulfonate)` | `pfas` |
| `PFECHS in Outfall 001A effluent` | `pfas` |
| `PFECHS in Outfall 001A wastewater` | `pfas` |
| `PFECHS in Outfall-001A wastewater sample` | `pfas` |
| `PFECHS in groundwater sample AH-001A` | `pfas` |
| `PFECHS in sample AH-001A` | `pfas` |
| `PFECHS in stormwater sample AH-STORM WATER` | `pfas` |
| `PFECHS in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFECHS in wastewater Outfall 001A` | `pfas` |
| `PFECHS, Outfall 001A` | `pfas` |
| `PFHpA (Perfluoroheptanoic Acid) in Discharge 001A-Grab water sample` | `pfas` |
| `PFHpA (Perfluoroheptanoic Acid) in Outfall-001A Grab` | `pfas` |
| `PFHpA (Perfluoroheptanoic Acid) in Outfall-001A wastewater sample` | `pfas` |
| `PFHpA (Perfluoroheptanoic Acid) in wastewater outfall` | `pfas` |
| `PFHpA (Perfluoroheptanoic Acid), Outfall 001A Grab` | `pfas` |
| `PFHpA (perfluoroheptanoic acid)` | `pfas` |
| `PFHpA (perfluoroheptanoic acid) in leachate DAF effluent` | `pfas` |
| `PFHpA (perfluoroheptanoic acid), Outfall 001A effluent` | `pfas` |
| `PFHpA (perfluoroheptanoic acid), Outfall 001A grab sample` | `pfas` |
| `PFHpA in DAF Effluent leachate sample` | `pfas` |
| `PFHpA in DAF effluent` | `pfas` |
| `PFHpA in Outfall 001A effluent` | `pfas` |
| `PFHpA in Outfall 001A wastewater` | `pfas` |
| `PFHpA in Outfall-001A effluent` | `pfas` |
| `PFHpA in Outfall-001A wastewater` | `pfas` |
| `PFHpA in groundwater sample AH-001A` | `pfas` |
| `PFHpA in sample AH-001A` | `pfas` |
| `PFHpA in stormwater sample AH-STORM WATER` | `pfas` |
| `PFHpA in treated wastewater effluent Outfall-001A` | `pfas` |
| `PFHpA in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFHpA in wastewater Outfall 001A` | `pfas` |
| `PFHpA in wastewater Outfall-001A` | `pfas` |
| `PFHpA in wastewater outfall sample` | `pfas` |
| `PFHpA with QA/QC recovery bias warning` | `pfas` |
| `PFHpA, Outfall 001A` | `pfas` |
| `PFHpA, Outfall 001A Grab` | `pfas` |
| `PFHpA, Outfall 001A field duplicate` | `qa_sample` |
| `PFHpS (perfluoroheptane sulfonic acid) in leachate DAF effluent` | `pfas` |
| `PFHpS in DAF Effluent leachate sample` | `pfas` |
| `PFHpS in DAF effluent` | `pfas` |
| `PFHxA` | `pfas` |
| `PFHxA (Perfluorohexanoic Acid) in Discharge 001A-Grab water sample` | `pfas` |
| `PFHxA (Perfluorohexanoic Acid) in Outfall-001A Grab` | `pfas` |
| `PFHxA (Perfluorohexanoic Acid) in Outfall-001A wastewater sample` | `pfas` |
| `PFHxA (Perfluorohexanoic Acid) in wastewater outfall` | `pfas` |
| `PFHxA (Perfluorohexanoic Acid), Outfall 001A Grab` | `pfas` |
| `PFHxA (Perfluorohexanoic acid) in sample P3-6 (W)` | `pfas` |
| `PFHxA (per- and polyfluoroalkyl substance) in Compost Pond` | `pfas` |
| `PFHxA (perfluorohexanoic acid)` | `pfas` |
| `PFHxA (perfluorohexanoic acid) in AHE raw leachate` | `pfas` |
| `PFHxA (perfluorohexanoic acid) in leachate DAF effluent` | `pfas` |
| `PFHxA (perfluorohexanoic acid), Outfall 001A effluent` | `pfas` |
| `PFHxA (perfluorohexanoic acid), Outfall 001A grab sample` | `pfas` |
| `PFHxA in Compost Pond YCUA-1` | `pfas` |
| `PFHxA in DAF Effluent leachate sample` | `pfas` |
| `PFHxA in DAF effluent` | `pfas` |
| `PFHxA in Outfall 001A effluent` | `pfas` |
| `PFHxA in Outfall 001A wastewater` | `pfas` |
| `PFHxA in Outfall-001A effluent` | `pfas` |
| `PFHxA in Outfall-001A wastewater` | `pfas` |
| `PFHxA in Outfall-001A wastewater sample` | `pfas` |
| `PFHxA in groundwater sample AH-001A` | `pfas` |
| `PFHxA in one residential well; significantly below draft MCL of 400,000 ppt` | `pfas` |
| `PFHxA in sample AH-001A` | `pfas` |
| `PFHxA in stormwater sample AH-STORM WATER` | `pfas` |
| `PFHxA in treated wastewater effluent Outfall-001A` | `pfas` |
| `PFHxA in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFHxA in wastewater Outfall 001A` | `pfas` |
| `PFHxA in wastewater Outfall-001A` | `pfas` |
| `PFHxA in wastewater outfall sample` | `pfas` |
| `PFHxA, Outfall 001A` | `pfas` |
| `PFHxA, Outfall 001A Grab` | `pfas` |
| `PFHxA, Outfall 001A field duplicate` | `qa_sample` |
| `PFHxS` | `pfas` |
| `PFHxS (Estimated Branched) in Outfall 001A wastewater` | `pfas` |
| `PFHxS (Estimated Branched) in wastewater Outfall 001A` | `pfas` |
| `PFHxS (Estimated Branched), Outfall 001A grab sample` | `pfas` |
| `PFHxS (Estimated Linear) in wastewater Outfall 001A` | `pfas` |
| `PFHxS (Perfluorohexane Sulfonic Acid) in Discharge 001A-Grab water sample` | `pfas` |
| `PFHxS (Perfluorohexane Sulfonic Acid) in Outfall-001A Grab` | `pfas` |
| `PFHxS (Perfluorohexane Sulfonic Acid) in Outfall-001A wastewater sample` | `pfas` |
| `PFHxS (Perfluorohexane Sulfonic Acid) in wastewater outfall` | `pfas` |
| `PFHxS (Perfluorohexane Sulfonic Acid), Outfall 001A Grab` | `pfas` |
| `PFHxS (Perfluorohexanesulfonic acid), sample SED-07` | `pfas` |
| `PFHxS (perfluorohexane sulfonate) in AHE raw leachate` | `pfas` |
| `PFHxS (perfluorohexane sulfonate), Outfall 001A grab sample` | `pfas` |
| `PFHxS (perfluorohexane sulfonic acid)` | `pfas` |
| `PFHxS (perfluorohexane sulfonic acid) in leachate DAF effluent` | `pfas` |
| `PFHxS (perfluorohexane sulfonic acid), Outfall 001A effluent` | `pfas` |
| `PFHxS Estimated Branched in wastewater outfall sample` | `pfas` |
| `PFHxS Estimated Linear in wastewater outfall sample` | `pfas` |
| `PFHxS concentration; exceeds DWC of 51 ng/L` | `pfas` |
| `PFHxS detection limit (EPA 537), water` | `pfas` |
| `PFHxS in DAF Effluent leachate sample` | `pfas` |
| `PFHxS in DAF effluent` | `pfas` |
| `PFHxS in Outfall 001A effluent` | `pfas` |
| `PFHxS in Outfall 001A wastewater` | `pfas` |
| `PFHxS in Outfall-001A effluent` | `pfas` |
| `PFHxS in Outfall-001A wastewater` | `pfas` |
| `PFHxS in groundwater sample AH-001A` | `pfas` |
| `PFHxS in sample AH-001A` | `pfas` |
| `PFHxS in stormwater sample AH-STORM WATER` | `pfas` |
| `PFHxS in treated wastewater effluent Outfall-001A` | `pfas` |
| `PFHxS in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFHxS in wastewater Outfall 001A` | `pfas` |
| `PFHxS in wastewater Outfall-001A` | `pfas` |
| `PFHxS in wastewater outfall sample` | `pfas` |
| `PFHxS, Outfall 001A` | `pfas` |
| `PFHxS, Outfall 001A Grab` | `pfas` |
| `PFHxS-BR (perfluorohexane sulfonic acid, branched isomer) in leachate DAF effluent` | `pfas` |
| `PFHxS-BR in DAF Effluent leachate sample` | `pfas` |
| `PFHxS-BR in DAF effluent` | `pfas` |
| `PFHxS-LN (Perfluorohexane Sulfonic Acid - LN) in wastewater outfall` | `pfas` |
| `PFHxS-LN (perfluorohexane sulfonic acid, linear isomer) in leachate DAF effluent` | `pfas` |
| `PFHxS-LN in DAF Effluent leachate sample` | `pfas` |
| `PFHxS-LN in DAF effluent` | `pfas` |
| `PFHxS-LN in Outfall 001A wastewater` | `pfas` |
| `PFHxS-Total (Perfluorohexanesulfonic acid) in sample P3-6 (W)` | `pfas` |
| `PFNA (perfluorononanoic acid) in AHE raw leachate` | `pfas` |
| `PFNA (perfluorononanoic acid) in leachate DAF effluent` | `pfas` |
| `PFNA in DAF Effluent leachate sample` | `pfas` |
| `PFNA in DAF effluent` | `pfas` |
| `PFNA in Outfall-001A effluent; non-detect below reporting limit` | `qa_sample` |
| `PFNA in stormwater sample AH-STORM WATER` | `pfas` |
| `PFNA in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFNA in wastewater Outfall 001A; flagged V+ (136% recovery, biased high)` | `qa_sample` |
| `PFNA non-detect` | `pfas` |
| `PFOA` | `pfas` |
| `PFOA (Perfluoroactanoic Acid), grab sample` | `pfas` |
| `PFOA (Perfluoroactanoic Acid), grab sample facility` | `pfas` |
| `PFOA (Perfluorooctanoic Acid)` | `pfas` |
| `PFOA (Perfluorooctanoic Acid) - Arbor Hills Remediation Area sample 001A` | `pfas` |
| `PFOA (Perfluorooctanoic Acid) - EGLE sample 001A` | `pfas` |
| `PFOA (Perfluorooctanoic Acid) - January quarterly grab` | `pfas` |
| `PFOA (Perfluorooctanoic Acid) in Discharge 001A-Grab water sample` | `pfas` |
| `PFOA (Perfluorooctanoic Acid) in Outfall-001A Grab` | `pfas` |
| `PFOA (Perfluorooctanoic Acid) in Outfall-001A wastewater sample` | `pfas` |
| `PFOA (Perfluorooctanoic Acid) in wastewater outfall` | `pfas` |
| `PFOA (Perfluorooctanoic Acid), Final Effluent` | `pfas` |
| `PFOA (Perfluorooctanoic Acid), Final Effluent (1)` | `pfas` |
| `PFOA (Perfluorooctanoic Acid), Outfall 001A Grab` | `pfas` |
| `PFOA (Perfluorooctanoic Acid), quarterly grab sample` | `pfas` |
| `PFOA (Perfluorooctanoic Acid); permit requires reporting` | `pfas` |
| `PFOA (Perfluorooctanoic acid)` | `pfas` |
| `PFOA (Perfluorooctanoic acid) - Effluent Pond` | `pfas` |
| `PFOA (perfluorooctanoic acid)` | `pfas` |
| `PFOA (perfluorooctanoic acid) in AHE raw leachate` | `pfas` |
| `PFOA (perfluorooctanoic acid) in leachate DAF effluent` | `pfas` |
| `PFOA (perfluorooctanoic acid) – highest detected concentration; no reasonable potential found` | `pfas` |
| `PFOA (perfluorooctanoic acid), Outfall 001A effluent` | `pfas` |
| `PFOA (perfluorooctanoic acid), Outfall 001A grab sample` | `pfas` |
| `PFOA (second grab)` | `pfas` |
| `PFOA - Effluent Pond (second sample point)` | `pfas` |
| `PFOA - Final Effluent (1)` | `pfas` |
| `PFOA - Pond East` | `pfas` |
| `PFOA - Pond West` | `pfas` |
| `PFOA - Pond-1 EAST` | `pfas` |
| `PFOA - Pond-1 WEST` | `pfas` |
| `PFOA - Roadway Drainage Ditch Southwest (Storm Event #1)` | `pfas` |
| `PFOA - Roadway Drainage Ditch Southwest (Storm Event #2)` | `pfas` |
| `PFOA Groundwater Soil Interface (GSI) limit` | `pfas` |
| `PFOA concentration in discharge` | `pfas` |
| `PFOA concentration, East side of pond` | `pfas` |
| `PFOA concentration, West side of pond` | `pfas` |
| `PFOA concentration; exceeds DWC of 8 ng/L` | `pfas` |
| `PFOA daily maximum limit` | `pfas` |
| `PFOA daily maximum limit for potential treated leachate discharge` | `pfas` |
| `PFOA daily maximum limit under proposed NPDES modification` | `pfas` |
| `PFOA detection limit (EPA 537), water` | `pfas` |
| `PFOA effluent limit proposed for NPDES permit modification` | `pfas` |
| `PFOA grab sample 001A` | `pfas` |
| `PFOA in DAF Effluent leachate sample` | `pfas` |
| `PFOA in DAF effluent` | `pfas` |
| `PFOA in Outfall 001A effluent` | `pfas` |
| `PFOA in Outfall 001A wastewater` | `pfas` |
| `PFOA in Outfall-001A effluent` | `pfas` |
| `PFOA in Outfall-001A wastewater` | `pfas` |
| `PFOA in Outfall-001A wastewater sample` | `pfas` |
| `PFOA in Pond 1 water, maximum of 14 samples, well below Rule 57 limit of 12,000 ng/L` | `pfas` |
| `PFOA in Pond 2 water, maximum detected concentration, well below Rule 57 limit of 12,000 ng/L` | `pfas` |
| `PFOA in discharge, below GSI limit of 12000 ng/L` | `pfas` |
| `PFOA in final effluent, maximum` | `pfas` |
| `PFOA in groundwater sample AH-001A` | `pfas` |
| `PFOA in sample AH-001A` | `pfas` |
| `PFOA in stormwater sample AH-STORM WATER` | `pfas` |
| `PFOA in surface water sample SW-1; below Rule 57 limit of 12,000 ng/L` | `pfas` |
| `PFOA in surface water sample SW-2; below Rule 57 limit of 12,000 ng/L` | `pfas` |
| `PFOA in treated wastewater effluent Outfall-001A` | `pfas` |
| `PFOA in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFOA in wastewater Outfall 001A` | `pfas` |
| `PFOA in wastewater Outfall-001A` | `pfas` |
| `PFOA in wastewater outfall sample` | `pfas` |
| `PFOA limit for treated leachate discharge under proposed NPDES permit` | `pfas` |
| `PFOA maximum concentration in Pond 1 water samples (14 samples collected Nov 2020–Apr 2021)` | `pfas` |
| `PFOA maximum concentration in Pond 2 water samples (10 samples)` | `pfas` |
| `PFOA permitted limit under anticipated NPDES modification` | `pfas` |
| `PFOA proposed daily maximum limit` | `pfas` |
| `PFOA, East pond` | `pfas` |
| `PFOA, Outfall 001A` | `pfas` |
| `PFOA, Outfall 001A Grab` | `pfas` |
| `PFOA, Outfall 001A field duplicate` | `qa_sample` |
| `PFOA, PFAS Impacted Pond East side` | `pfas` |
| `PFOA, PFAS Impacted Pond West side` | `pfas` |
| `PFOA, Pond-1 EAST` | `pfas` |
| `PFOA, Pond-1 WEST` | `pfas` |
| `PFOA, West pond` | `pfas` |
| `PFOS` | `pfas` |
| `PFOS (Estimated Branched) in wastewater Outfall 001A` | `pfas` |
| `PFOS (Estimated Linear) in wastewater Outfall 001A` | `pfas` |
| `PFOS (Perfluorooctane Sulfonate)` | `pfas` |
| `PFOS (Perfluorooctane Sulfonate), Final Effluent` | `pfas` |
| `PFOS (Perfluorooctane Sulfonate), quarterly grab sample` | `pfas` |
| `PFOS (Perfluorooctane Sulfonate); permit requires reporting` | `pfas` |
| `PFOS (Perfluorooctane Sulfonic Acid) in Outfall-001A Grab` | `pfas` |
| `PFOS (Perfluorooctane Sulfonic Acid) in Outfall-001A wastewater sample` | `pfas` |
| `PFOS (Perfluorooctane Sulfonic Acid) in wastewater outfall` | `pfas` |
| `PFOS (Perfluorooctane Sulfonic Acid), Outfall 001A Grab` | `pfas` |
| `PFOS (Perfluorooctane sulfonate), sample SED-08` | `pfas` |
| `PFOS (Perfluorooctanesulfonic Acid)` | `pfas` |
| `PFOS (Perfluorooctanesulfonic Acid) - Arbor Hills Remediation Area sample 001A` | `pfas` |
| `PFOS (Perfluorooctanesulfonic Acid) - EGLE sample 001A` | `pfas` |
| `PFOS (Perfluorooctanesulfonic Acid), grab sample` | `pfas` |
| `PFOS (Perfluorooctanesulfonic Acid), grab sample facility` | `pfas` |
| `PFOS (perfluorooctane sulfonate) in AHE raw leachate` | `pfas` |
| `PFOS (perfluorooctane sulfonic acid)` | `pfas` |
| `PFOS (perfluorooctane sulfonic acid) in leachate DAF effluent` | `pfas` |
| `PFOS (perfluorosulfonic acid) – highest detected concentration; no reasonable potential found` | `pfas` |
| `PFOS (second grab)` | `pfas` |
| `PFOS - Final Effluent (1)` | `pfas` |
| `PFOS - Roadway Drainage Ditch Southwest (Storm Event #1)` | `pfas` |
| `PFOS - Roadway Drainage Ditch Southwest (Storm Event #2)` | `pfas` |
| `PFOS Estimated Branched in wastewater outfall sample` | `pfas` |
| `PFOS Estimated Linear in wastewater outfall sample` | `pfas` |
| `PFOS GSIP criterion for sediment sample SED-1; measured value exceeded this limit` | `pfas` |
| `PFOS Michigan baseline range — high end` | `pfas` |
| `PFOS Michigan baseline range — low end` | `pfas` |
| `PFOS Water Quality Standard` | `pfas` |
| `PFOS at stormwater discharge point from AHLF, sampled by EGLE` | `pfas` |
| `PFOS concentration (non-detect)` | `pfas` |
| `PFOS concentration in Pond 2 water sample (composite sample 1)` | `pfas` |
| `PFOS concentration in Pond 2 water sample (composite sample 2)` | `pfas` |
| `PFOS concentration in Pond 5 Mile` | `pfas` |
| `PFOS concentration in Pond 5 Mile stormwater` | `pfas` |
| `PFOS concentration in Pond Napier` | `pfas` |
| `PFOS concentration in Pond Napier stormwater` | `pfas` |
| `PFOS concentration; exceeds DWC of 16 ng/L` | `pfas` |
| `PFOS daily maximum limit` | `pfas` |
| `PFOS daily maximum limit for potential treated leachate discharge` | `pfas` |
| `PFOS daily maximum limit under proposed NPDES modification` | `pfas` |
| `PFOS detected above water quality standard in detention pond` | `pfas` |
| `PFOS detected above water quality standard in stormwater detention pond; exact concentration not stated` | `pfas` |
| `PFOS detection limit (EPA 537), water` | `pfas` |
| `PFOS effluent limit proposed for NPDES permit modification` | `pfas` |
| `PFOS grab sample 001A` | `pfas` |
| `PFOS high end of Johnson Creek range over past four years` | `pfas` |
| `PFOS in Arbor Hills Stormwater Pond, up to 33X allowable levels` | `pfas` |
| `PFOS in DAF Effluent leachate sample` | `pfas` |
| `PFOS in DAF effluent` | `pfas` |
| `PFOS in Johnson Creek tributary directly downstream of landfill, sampled by The Conservancy Initiative` | `pfas` |
| `PFOS in Johnson Drain at 6 Mile Road` | `pfas` |
| `PFOS in Johnson Drain at 6 Mile Road, sampled by EGLE` | `pfas` |
| `PFOS in Outfall 001A effluent` | `pfas` |
| `PFOS in Outfall 001A wastewater` | `pfas` |
| `PFOS in Outfall-001A effluent` | `pfas` |
| `PFOS in Outfall-001A effluent; detected in method blank` | `qa_sample` |
| `PFOS in Outfall-001A wastewater` | `pfas` |
| `PFOS in Outfall-001A wastewater sample` | `pfas` |
| `PFOS in Pond 1 sediment, maximum detected concentration` | `pfas` |
| `PFOS in Pond 1 water, maximum concentration, above Rule 57 limit of 12 ng/L` | `pfas` |
| `PFOS in Pond 1, post-fire November 2016` | `pfas` |
| `PFOS in Pond 2 water, composite sample concentration, above Rule 57 limit` | `pfas` |
| `PFOS in Pond 2 water, maximum composite sample concentration, significantly above Rule 57 limit` | `pfas` |
| `PFOS in Pond 2, post-fire November 2016; majority of firefighting wastewater received here` | `pfas` |
| `PFOS in Pond 2-East (6") soil sample` | `pfas` |
| `PFOS in Pond 5 Mile stormwater sample` | `pfas` |
| `PFOS in Pond Napier stormwater sample` | `pfas` |
| `PFOS in brown trout fillets from Johnson Drain (maximum observed)` | `pfas` |
| `PFOS in brown trout fillets from Johnson Drain (minimum observed)` | `pfas` |
| `PFOS in brown trout fillets, high range, Fish Hatchery Park` | `pfas` |
| `PFOS in brown trout fillets, low range, Fish Hatchery Park` | `pfas` |
| `PFOS in discharge samples (high end)` | `pfas` |
| `PFOS in discharge samples (low end)` | `pfas` |
| `PFOS in final effluent, maximum` | `pfas` |
| `PFOS in groundwater sample AH-001A` | `pfas` |
| `PFOS in pond south of railroad tracks (Pond-1 East, lowest detected); exceeds Rule 57 limit of 12 ng/L` | `pfas` |
| `PFOS in pond south of railroad tracks (Pond-1 West, highest detected); exceeds Rule 57 limit of 12 ng/L` | `pfas` |
| `PFOS in sample AH-001A` | `pfas` |
| `PFOS in site pond samples (high end of range)` | `pfas` |
| `PFOS in site pond samples (low end of range)` | `pfas` |
| `PFOS in soil sample 0.5-1 feet bgs near leachate tank farm; exceeds GSIP criterion of 0.24 µg/kg` | `pfas` |
| `PFOS in soil sample 2-3 feet bgs near leachate tank farm; exceeds GSIP criterion of 0.24 µg/kg` | `pfas` |
| `PFOS in storm water discharge` | `pfas` |
| `PFOS in stormwater detention pond; 33× exceedance of 12 µg/L Michigan water quality standard` | `pfas` |
| `PFOS in stormwater detention pond; 33× over 12 µg/L limit` | `pfas` |
| `PFOS in stormwater discharge from AHLF to unnamed tributary to Johnson Drain` | `pfas` |
| `PFOS in stormwater sample AH-STORM WATER` | `pfas` |
| `PFOS in surface water sample SW-1; exceeds Rule 57 limit of 12 ng/L` | `pfas` |
| `PFOS in surface water sample SW-2; exceeds Rule 57 limit of 12 ng/L; flagged as above calibration range` | `pfas` |
| `PFOS in treated wastewater effluent Outfall-001A` | `pfas` |
| `PFOS in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFOS in unnamed tributary to Johnson Creek downstream of landfill (TCI sample)` | `pfas` |
| `PFOS in wastewater Outfall 001A` | `pfas` |
| `PFOS in wastewater Outfall-001A` | `pfas` |
| `PFOS in wastewater outfall sample` | `pfas` |
| `PFOS limit for treated leachate discharge under proposed NPDES permit` | `pfas` |
| `PFOS low end of Johnson Creek range over past four years` | `pfas` |
| `PFOS maximum concentration in Pond 1 sediment samples (depth up to 3.5 ft); range ND to 530 ng/kg` | `pfas` |
| `PFOS maximum concentration in Pond 1 water samples (14 samples); range ND to 47 ng/L` | `pfas` |
| `PFOS non-detect, below GSI limit of 12 ng/L` | `pfas` |
| `PFOS permitted limit under anticipated NPDES modification` | `pfas` |
| `PFOS proposed daily maximum limit` | `pfas` |
| `PFOS samples from Arbor Hills Stormwater Pond, March 2020, up to 33× allowable levels` | `pfas` |
| `PFOS upstream sample exceeding Michigan HNV for non-drinking water` | `pfas` |
| `PFOS water quality standard` | `pfas` |
| `PFOS water quality standard (no mixing zone allowed for bioaccumulative chemical of concern)` | `pfas` |
| `PFOS water quality standard cited as compliance target` | `pfas` |
| `PFOS water quality standard limit` | `pfas` |
| `PFOS+PFOA in leachate sample; no federal or state standard exists for leachate` | `pfas` |
| `PFOS+PFOA; confirmation sample; exceeds 70 ppt criterion` | `pfas` |
| `PFOS+PFOA; initial sample` | `pfas` |
| `PFOS, Outfall 001A` | `pfas` |
| `PFOS, Outfall 001A Grab` | `pfas` |
| `PFOS, sample BG-SED-04` | `pfas` |
| `PFOS, sample SED-01` | `pfas` |
| `PFOS, sample SED-02` | `pfas` |
| `PFOS, sample SED-03` | `pfas` |
| `PFOS, sample SED-03 (repeated)` | `pfas` |
| `PFOS, sample SED-04` | `pfas` |
| `PFOS, sample SED-05` | `pfas` |
| `PFOS, sample SED-05 (repeated)` | `pfas` |
| `PFOS, sample SED-06` | `pfas` |
| `PFOS, sample SED-07 (repeated)` | `pfas` |
| `PFOS, sample SED-08 (repeated)` | `pfas` |
| `PFOS-BR (PFOS branched isomer) in leachate DAF effluent` | `pfas` |
| `PFOS-BR in DAF Effluent leachate sample` | `pfas` |
| `PFOS-BR in DAF effluent` | `pfas` |
| `PFOS-BR in Outfall 001A wastewater` | `pfas` |
| `PFOS-LN (PFOS linear isomer) in leachate DAF effluent` | `pfas` |
| `PFOS-LN in DAF Effluent leachate sample` | `pfas` |
| `PFOS-LN in DAF effluent` | `pfas` |
| `PFOS-LN in Outfall 001A wastewater` | `pfas` |
| `PFOS-Total (Perfluorooctane sulfonic acid)` | `pfas` |
| `PFOS-Total (Perfluorooctanesulfonic acid) in sample P3-6 (W)` | `pfas` |
| `PFOS-Total - Effluent Pond` | `pfas` |
| `PFOS-Total - Pond East` | `pfas` |
| `PFOS-Total - Pond West` | `pfas` |
| `PFOS-Total - Pond-1 EAST` | `pfas` |
| `PFOS-Total - Pond-1 WEST` | `pfas` |
| `PFOS-Total concentration, East side of pond` | `pfas` |
| `PFOS-Total concentration, West side of pond` | `pfas` |
| `PFOS-Total in sample Outfall-CR (W)` | `pfas` |
| `PFOS-Total in sample P3-15 (W)` | `pfas` |
| `PFOS-Total, East pond` | `pfas` |
| `PFOS-Total, PFAS Impacted Pond East side` | `pfas` |
| `PFOS-Total, PFAS Impacted Pond West side` | `pfas` |
| `PFOS-Total, Pond-1 EAST` | `pfas` |
| `PFOS-Total, Pond-1 WEST` | `pfas` |
| `PFOS-Total, West pond` | `pfas` |
| `PFPeA` | `pfas` |
| `PFPeA (Perfluoropentanoic Acid) in Discharge 001A-Grab water sample` | `pfas` |
| `PFPeA (Perfluoropentanoic Acid) in Outfall-001A Grab` | `pfas` |
| `PFPeA (Perfluoropentanoic Acid) in Outfall-001A wastewater sample` | `pfas` |
| `PFPeA (Perfluoropentanoic Acid) in wastewater outfall` | `pfas` |
| `PFPeA (Perfluoropentanoic Acid), Outfall 001A Grab` | `pfas` |
| `PFPeA (per- and polyfluoroalkyl substance) in Compost Pond` | `pfas` |
| `PFPeA (perfluoropentanoic acid)` | `pfas` |
| `PFPeA (perfluoropentanoic acid) in leachate DAF effluent` | `pfas` |
| `PFPeA (perfluoropentanoic acid), Outfall 001A effluent` | `pfas` |
| `PFPeA (perfluoropentanoic acid), Outfall 001A grab sample` | `pfas` |
| `PFPeA in Compost Pond YCUA-1` | `pfas` |
| `PFPeA in DAF Effluent leachate sample` | `pfas` |
| `PFPeA in DAF effluent` | `pfas` |
| `PFPeA in Outfall 001A effluent` | `pfas` |
| `PFPeA in Outfall 001A wastewater` | `pfas` |
| `PFPeA in Outfall-001A effluent` | `pfas` |
| `PFPeA in Outfall-001A wastewater` | `pfas` |
| `PFPeA in Outfall-001A wastewater sample` | `pfas` |
| `PFPeA in groundwater sample AH-001A` | `pfas` |
| `PFPeA in sample AH-001A` | `pfas` |
| `PFPeA in stormwater sample AH-STORM WATER` | `pfas` |
| `PFPeA in treated wastewater effluent Outfall-001A` | `pfas` |
| `PFPeA in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFPeA in wastewater Outfall 001A` | `pfas` |
| `PFPeA in wastewater Outfall-001A` | `pfas` |
| `PFPeA in wastewater outfall sample` | `pfas` |
| `PFPeA, Outfall 001A` | `pfas` |
| `PFPeA, Outfall 001A Grab` | `pfas` |
| `PFPeA, Outfall 001A field duplicate` | `qa_sample` |
| `PFPeS (Perfluoropentane Sulfonic Acid) in wastewater outfall` | `pfas` |
| `PFPeS (perfluoropentane sulfonic acid) in leachate DAF effluent` | `pfas` |
| `PFPeS in DAF Effluent leachate sample` | `pfas` |
| `PFPeS in DAF effluent` | `pfas` |
| `PFPeS in Outfall 001A wastewater` | `pfas` |
| `PFPeS in stormwater sample AH-STORM WATER` | `pfas` |
| `PFPeS in treated wastewater effluent, Outfall-001A` | `pfas` |
| `PFPeS in wastewater Outfall 001A` | `pfas` |
| `PFTeA (per- and polyfluoroalkyl substance) in Compost Pond` | `pfas` |
| `PM10 actual emissions` | `particulate_matter` |
| `PM10 and PM2.5 actual emissions <1` | `particulate_matter` |
| `PM10 potential emissions` | `particulate_matter` |
| `PM2.5 actual emissions` | `particulate_matter` |
| `PM2.5 potential emissions` | `particulate_matter` |
| `PM₂.₅ 24-hour NAAQS limit` | `particulate_matter` |
| `PM₂.₅ 24-hour combined impact scenario 3b` | `particulate_matter` |
| `PSD threshold for significant net SO2 emissions increase` | `sulfur_dioxide` |
| `PTI 68-23 Condition II.1 diesel fuel annual limit based on 12-month rolling period` | `operational_capacity` |
| `PTI 68-23 Condition III.3 diesel fuel use limit for turbine startup (FGTURBINES)` | `operational_capacity` |
| `Particulate Matter (PM)` | `particulate_matter` |
| `Peak ammonia as nitrogen concentration, single day (12/15 and 12/16)` | `ammonia_nitrogen` |
| `Peak landfill gas projected for 2029` | `operational_capacity` |
| `Penalty amount in Violation Notice` | `event_status` |
| `Percentage of 130+ samples in STSWCS exceeding PFOS standards` | `exceedances_count` |
| `Percentage of 130+ stormwater samples with PFOS above water quality standards, December 2021 Short Term Stormwater Characterization Study` | `exceedances_count` |
| `Percentage of 130+ stormwater samples with PFOS exceeding water quality standards (December 2021 Short Term Stormwater Characterization Study)` | `exceedances_count` |
| `Perfluorobutanesulfonic acid (PFBS)` | `pfas` |
| `Perfluorobutanesulfonic acid (PFBS), Final Effluent (1); permit (Report)` | `pfas` |
| `Perfluorobutanoic acid (PFBA)` | `pfas` |
| `Perfluoroheptanoic acid (PFHpA)` | `pfas` |
| `Perfluorohexanesulfonic acid (PFHxS), flagged B (found in blank)` | `qa_sample` |
| `Perfluorohexanoic acid (PFHxA)` | `pfas` |
| `Perfluorononanoic acid (PFNA), flagged J` | `pfas` |
| `Perfluorooctane Sulfonate (PFOS)` | `pfas` |
| `Perfluorooctane Sulfonate (PFOS), Final Effluent (1); permit (Report)` | `pfas` |
| `Perfluorooctane sulfonate (PFOS), flagged J` | `pfas` |
| `Perfluorooctanoic Acid (PFOA)` | `pfas` |
| `Perfluorooctanoic Acid (PFOA), Final Effluent (1); permit (Report)` | `pfas` |
| `Perfluorooctanoic acid (PFOA)` | `pfas` |
| `Perfluoropentanesulfonic acid (PFPeS)` | `pfas` |
| `Perfluoropentanoic acid (PFPeA)` | `pfas` |
| `Perimeter H2S (hydrogen sulfide) Action Level as rolling 15-minute average` | `hydrogen_sulfide` |
| `Perimeter Methane Action Level (15-minute rolling average)` | `methane` |
| `Perimeter Methane Action Level (rolling 15-minute average)` | `methane` |
| `Perimeter monitor action level alarm for hydrogen sulfide (H2S)` | `surface_emissions` |
| `Perimeter monitor action level alarm for methane` | `surface_emissions` |
| `Perimeter monitor action level alarm threshold for hydrogen sulfide (H2S)` | `hydrogen_sulfide` |
| `Perimeter monitor action-level alarm threshold for H2S` | `hydrogen_sulfide` |
| `Perimeter monitor methane action level threshold; frequent alarms exceeded this level starting December 2, 2025` | `methane` |
| `Perimeter monitors MS-2, MS-3, MS-4, MS-5, MS-6 action level threshold for methane (CH4) exceedances` | `surface_emissions` |
| `Permit evaluated at this flow rate for EU5000CFM flare` | `operational_capacity` |
| `Permit limit for hydrogen sulfide and total reduced sulfur in LFG to flares; H2S monitoring required if exceed 326 ppm (80% of limit)` | `hydrogen_sulfide` |
| `Permitted discharge volume of treated groundwater and compost pile runoff` | `flow_wastewater` |
| `Permitted sulfur (H2S/TRS) concentration threshold in landfill gas for monitoring escalation` | `trs` |
| `Permitted sulfur content limit for landfill gas burned in flares` | `trs` |
| `Phenol in leachate effluent` | `other` |
| `Phenolics - below detection limit (< 0.010)` | `nmoc_voc` |
| `Phenolics, below detection limit <0.010` | `nmoc_voc` |
| `Phenols, sample AH (not detected)` | `other` |
| `Phosphorus daily load limit` | `phosphorus` |
| `Phosphorus monthly average concentration limit` | `phosphorus` |
| `Phosphorus permit limit, maximum month` | `phosphorus` |
| `Phosphorus permit limit, maximum month concentration` | `phosphorus` |
| `Phosphorus total, exceeded 0.8 lbs/day limit` | `phosphorus` |
| `Phosphorus total, exceeded 1.0 mg/L limit` | `phosphorus` |
| `Phosphorus, Outfall 001A, concentration` | `phosphorus` |
| `Phosphorus, Outfall 001A, loading` | `phosphorus` |
| `Phosphorus, Total (below detection limit)` | `phosphorus` |
| `Phosphorus-Total (as P) in Outfall 001A Composite` | `phosphorus` |
| `Phosphorus-Total (as P) in Outfall 001A composite wastewater` | `phosphorus` |
| `Phosphorus-Total (as P), Outfall 001A` | `phosphorus` |
| `Phosphorus-Total (as P), Outfall 001A Composite` | `phosphorus` |
| `Pit Raider discharge concentration on Day 1; exceeds WQBEL limit` | `other` |
| `Pit Raider requested discharge concentration (average over 3-4 months)` | `other` |
| `Planned length of North side horizontal collector project` | `event_status` |
| `Planned length of West Haul road project starting September 30, 2019` | `event_status` |
| `Planned total blower capacity (3 blowers at 4000 SCFM each)` | `operational_capacity` |
| `Plant capacity estimate lower range per operator` | `operational_capacity` |
| `Plant capacity estimate upper range per operator` | `operational_capacity` |
| `Potential NOx emissions` | `nitrogen_oxides` |
| `Previous NOX emissions allowable limit` | `nitrogen_oxides` |
| `Previous available vacuum measurement before new header installation` | `pressure_vacuum` |
| `Primary leachate collection system Cell 4 threshold — violation triggered when levels exceed this` | `event_status` |
| `Proposed 12-month rolling average Hg limit for permit reissuance` | `mercury` |
| `Proposed H2S limit in landfill gas` | `hydrogen_sulfide` |
| `Proposed compensatory mitigation wetland (PEM 3.21 ac + PFO 3.28 ac)` | `other` |
| `Proposed depth of Shelby tube soil samples in southern stormwater ditch` | `operational_capacity` |
| `Proposed discharge concentration for Pit Raider via Outfall 001` | `other` |
| `Proposed discharge volume from compost leachate pond` | `flow_wastewater` |
| `Pump Daily Avg - Dec 1, 2022Q4` | `other` |
| `Pump Daily Avg - Dec 2, 2022Q4` | `other` |
| `Pump Daily Avg - Nov 1, 2022Q4` | `other` |
| `Pump Daily Avg - Nov 2, 2022Q4` | `other` |
| `Pump Daily Avg - Oct 1, 2022Q4` | `other` |
| `Pump Daily Avg - Oct 2, 2022Q4` | `other` |
| `Pump Daily Avg - Sep 1, 2022Q4; units unclear from table context` | `other` |
| `Pump Daily Avg - Sep 2, 2022Q4` | `other` |
| `Pump depth reduced due to goo in bottom of well` | `well_operational` |
| `Q3 2024 (Aug 12-15) surface emissions exceedances of 500 ppm methane` | `exceedances_count` |
| `Q3 surface methane exceedances (Aug 12-15)` | `exceedances_count` |
| `Q4 2024 (Nov 12-14) surface emissions exceedances of 500 ppm methane` | `exceedances_count` |
| `Q4 surface methane exceedances (Nov 12-14)` | `exceedances_count` |
| `RACM (Type II Waste) estimated for removal` | `other` |
| `RNG plant maximum landfill gas processing capacity` | `operational_capacity` |
| `Rainfall the night prior to odor incident` | `wind_odor` |
| `Rated LFG inlet capacity for EUENCLOSEDFLARE1-S2` | `operational_capacity` |
| `Rated LFG inlet capacity for EUENCLOSEDFLARE2-S2` | `operational_capacity` |
| `Rated design capacity of EUENCLOSEDFLARE1-S2` | `operational_capacity` |
| `Rated design capacity of EUENCLOSEDFLARE2-S2` | `operational_capacity` |
| `Raw landfill gas flow measured at gas plant` | `operational_capacity` |
| `Recommended Heat Addition Limit for March` | `other` |
| `Recommended Level Currently Achievable (LCA) limit for mercury (12-month rolling average)` | `mercury` |
| `Recommended available cyanide daily maximum limit` | `cyanide` |
| `Recommended available cyanide monthly average limit` | `cyanide` |
| `Recommended daily maximum limit for available cyanide` | `cyanide` |
| `Recommended level currently achievable (LCA) limit for total mercury` | `mercury` |
| `Recommended monthly average limit for available cyanide` | `cyanide` |
| `Recommended total mercury LCA limit, 12-month rolling average` | `mercury` |
| `Recommended total mercury limit` | `mercury` |
| `Record gas flow through flares` | `operational_capacity` |
| `Regulatory limit for methane surface breakouts; document states numerous locations measured exceeding this limit` | `methane` |
| `Regulatory maximum leachate head depth on primary liner, R 299.4432(1)` | `other` |
| `Relative humidity` | `event_status` |
| `Relative humidity at 10:23 AM` | `other` |
| `Report limit for phenolics analysis` | `other` |
| `Reporting limit for mercury analysis` | `mercury` |
| `Requested discharge volume to unnamed tributary to Johnson Drain via outfall 001` | `flow_wastewater` |
| `Required minimum enclosed flare stack height above ground` | `other` |
| `Required vacuum setpoint for GCCS during normal AHE operations` | `pressure_vacuum` |
| `Residue, Dissolved @ 180°C` | `tds` |
| `Residue, Suspended` | `tss` |
| `SEM exceedances >500 ppm above background` | `exceedances_count` |
| `SEM exceedances above 500 ppm methane` | `exceedances_count` |
| `SEM hits at ≥500 ppm methane above background` | `exceedances_count` |
| `SEM hits documented during surface emission monitoring survey` | `exceedances_count` |
| `SO2 12-month rolling basis limit` | `sulfur_dioxide` |
| `SO2 12-month rolling limit for FGPROJECT23` | `sulfur_dioxide` |
| `SO2 12-month rolling limit for FGPROJECT23 (all four turbines combined)` | `sulfur_dioxide` |
| `SO2 NSPS Subpart GG/KKKK limit` | `sulfur_dioxide` |
| `SO2 ROP permit limit for turbine 4` | `sulfur_dioxide` |
| `SO2 ROP permit limit for turbines 1–3` | `sulfur_dioxide` |
| `SO2 ROP permit limit per duct burner` | `sulfur_dioxide` |
| `SO2 ROP permit limit per duct burner annual` | `sulfur_dioxide` |
| `SO2 ROP permit limit per turbine annual` | `sulfur_dioxide` |
| `SO2 Run 1, EGT1, preliminary annual projection` | `sulfur_dioxide` |
| `SO2 Run 1, EGT1, preliminary result` | `sulfur_dioxide` |
| `SO2 Turbine 1, 12-month rolling, limit 12.5 TPY` | `sulfur_dioxide` |
| `SO2 Turbine 2, 12-month rolling, limit 12.5 TPY` | `sulfur_dioxide` |
| `SO2 Turbine 3, 12-month rolling, limit 12.5 TPY` | `sulfur_dioxide` |
| `SO2 Turbine 4 stack test average, limit 0.9 lb/MW-hr` | `sulfur_dioxide` |
| `SO2 actual emissions` | `sulfur_dioxide` |
| `SO2 actual emissions <1` | `sulfur_dioxide` |
| `SO2 allowable emission limit alternative for EUTURBINE4, PTI 68-23A V2.0` | `sulfur_dioxide` |
| `SO2 allowable emission limit for EUTURBINE4, PTI 68-23A V2.0` | `sulfur_dioxide` |
| `SO2 allowable emission limit for FGTURBINES, PTI 68-23A V2.0` | `sulfur_dioxide` |
| `SO2 analyzer reading at 11:52 AM, above limit` | `sulfur_dioxide` |
| `SO2 annual limit for each duct burner` | `sulfur_dioxide` |
| `SO2 annual limit for each turbine` | `sulfur_dioxide` |
| `SO2 annual permit limit for each duct burner` | `sulfur_dioxide` |
| `SO2 annual permit limit for each turbine` | `sulfur_dioxide` |
| `SO2 calendar year 2020 MAERS report` | `sulfur_dioxide` |
| `SO2 concentration (dry basis)` | `sulfur_dioxide` |
| `SO2 concentration Test 1` | `sulfur_dioxide` |
| `SO2 concentration Test 2` | `sulfur_dioxide` |
| `SO2 concentration Test 3` | `sulfur_dioxide` |
| `SO2 concentration from stack test` | `sulfur_dioxide` |
| `SO2 concentration from stack test EU-TURBINE4-S3` | `sulfur_dioxide` |
| `SO2 concentration three-test average` | `sulfur_dioxide` |
| `SO2 duct burner 1, May 29–June 1 2018 stack test` | `sulfur_dioxide` |
| `SO2 duct burner 1, October 16–19 2018 retest` | `sulfur_dioxide` |
| `SO2 duct burner 2, May 29–June 1 2018 stack test` | `sulfur_dioxide` |
| `SO2 duct burner 2, October 16–19 2018 retest` | `sulfur_dioxide` |
| `SO2 duct burner 3, May 29–June 1 2018 stack test` | `sulfur_dioxide` |
| `SO2 duct burner 3, October 16–19 2018 retest` | `sulfur_dioxide` |
| `SO2 emission limit` | `sulfur_dioxide` |
| `SO2 emission limit based on 440 ppmv sulfur content, 8760 hours/year operation` | `sulfur_dioxide` |
| `SO2 emission limit exceedances cited in EPA Findings of Violation (September 29, 2016 and June 4, 2018) but specific measured values not stated in this document` | `event_status` |
| `SO2 emission limit for EUOPENFLARE_TEMP, based on 500 ppmv sulfur content, 8760 hr/yr operation` | `sulfur_dioxide` |
| `SO2 emission limit from ROP0000224 v2.2` | `sulfur_dioxide` |
| `SO2 emission limit, PTI No. 179-13` | `sulfur_dioxide` |
| `SO2 emission rate` | `sulfur_dioxide` |
| `SO2 emission rate (hourly) from stack test` | `sulfur_dioxide` |
| `SO2 emission rate (lb/MWhr), exceeds permit limit of 0.9` | `sulfur_dioxide` |
| `SO2 emission rate from EU-TURBINE4-S3 stack test` | `sulfur_dioxide` |
| `SO2 emission rate from EUTURBINE4-S3` | `sulfur_dioxide` |
| `SO2 emission rate from stack test` | `sulfur_dioxide` |
| `SO2 emission rate, McGill flare exhaust` | `sulfur_dioxide` |
| `SO2 emission rate; exceeds permit limit` | `sulfur_dioxide` |
| `SO2 emissions Test 1` | `sulfur_dioxide` |
| `SO2 emissions Test 2` | `sulfur_dioxide` |
| `SO2 emissions Test 3` | `sulfur_dioxide` |
| `SO2 emissions from EU-TURBINE4-S3 (alternate unit)` | `sulfur_dioxide` |
| `SO2 emissions from EU-TURBINE4-S3 stack test` | `sulfur_dioxide` |
| `SO2 emissions limit for FGTURBINES` | `sulfur_dioxide` |
| `SO2 emissions three-test average` | `sulfur_dioxide` |
| `SO2 emissions, 12-month rolling average for FGPROJECT23` | `sulfur_dioxide` |
| `SO2 hourly limit for EUTURBINE4` | `sulfur_dioxide` |
| `SO2 hourly limit for FGTURBINES (normal operation with duct burner)` | `sulfur_dioxide` |
| `SO2 limit Turbine 4` | `sulfur_dioxide` |
| `SO2 limit Turbines 1-3` | `sulfur_dioxide` |
| `SO2 limit for EUTURBINE4` | `sulfur_dioxide` |
| `SO2 limit for EUTURBINE4-S3` | `sulfur_dioxide` |
| `SO2 limit for FGDUCTBURNERS-S3 duct burners` | `sulfur_dioxide` |
| `SO2 limit for FGTURBINES-S3 turbines` | `sulfur_dioxide` |
| `SO2 limit in PTI 19-17 and PTI 17-17A` | `sulfur_dioxide` |
| `SO2 measured in stack test EUTURBINE4-S3; limit 0.9 ppm or 0.15 (FAILED)` | `sulfur_dioxide` |
| `SO2 minimum odor threshold` | `sulfur_dioxide` |
| `SO2 normal operation for FGTURBINES` | `sulfur_dioxide` |
| `SO2 permit limit` | `sulfur_dioxide` |
| `SO2 permit limit (RO Permit MI-ROP-N2688-2011)` | `sulfur_dioxide` |
| `SO2 permit limit for EGT turbines` | `sulfur_dioxide` |
| `SO2 permit limit for EU-TURBINE4-S3` | `sulfur_dioxide` |
| `SO2 permit limit for EU-TURBINE4-S3 (alternate unit)` | `sulfur_dioxide` |
| `SO2 permit limit for FGDUCTBURNERS-S3` | `sulfur_dioxide` |
| `SO2 permit limit for FGTURBINES-S3` | `sulfur_dioxide` |
| `SO2 permit limit, ROP Condition I.6` | `sulfur_dioxide` |
| `SO2 permit limit, Turbine 4` | `sulfur_dioxide` |
| `SO2 permit limit, Turbines 1, 2, 3` | `sulfur_dioxide` |
| `SO2 permitted limit` | `sulfur_dioxide` |
| `SO2 pollutant limit, Turbine 4` | `sulfur_dioxide` |
| `SO2 pollutant limit, Turbine 4 (alternate)` | `sulfur_dioxide` |
| `SO2 pollutant limit, Turbines 1–3` | `sulfur_dioxide` |
| `SO2 potential emissions` | `sulfur_dioxide` |
| `SO2 preliminary result after Run #2, appears to exceed permit limit` | `sulfur_dioxide` |
| `SO2 reading with ductburner operating on EGT2` | `sulfur_dioxide` |
| `SO2 reading without ductburner on EGT tests` | `sulfur_dioxide` |
| `SO2 stack test Turbine 1, measured vs 2.9 lb/hr limit` | `sulfur_dioxide` |
| `SO2 stack test Turbine 2, measured vs 2.9 lb/hr limit` | `sulfur_dioxide` |
| `SO2 stack test Turbine 3, measured vs 2.9 lb/hr limit` | `sulfur_dioxide` |
| `SO2 turbine 1, May 29–June 1 2018 stack test` | `sulfur_dioxide` |
| `SO2 turbine 1, October 16–19 2018 retest` | `sulfur_dioxide` |
| `SO2 turbine 2, May 29–June 1 2018 stack test` | `sulfur_dioxide` |
| `SO2 turbine 2, October 16–19 2018 retest` | `sulfur_dioxide` |
| `SO2 turbine 3, May 29–June 1 2018 stack test` | `sulfur_dioxide` |
| `SO2 turbine 3, October 16–19 2018 retest` | `sulfur_dioxide` |
| `SO2 turbine 4 (Solar Taurus), May 29–June 1 2018 stack test` | `sulfur_dioxide` |
| `SO2 turbine 4 (Solar Taurus), October 16–19 2018 retest` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 1, Run 1` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 1, Run 2` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 1, Run 3` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 1, Three-Run Average` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 2, Run 1` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 2, Run 2` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 2, Run 3` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 2, Three-Run Average` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 3, Run 1` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 3, Run 2` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 3, Run 3` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 3, Three-Run Average` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 4, Run 1` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 4, Run 2` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 4, Run 3` | `sulfur_dioxide` |
| `SO2, ASTM D5504/D3588, Turbine 4, Three-Run Average` | `sulfur_dioxide` |
| `SO2, EGT Turbine #1, Duct Burner OFF` | `sulfur_dioxide` |
| `SO2, EGT Turbine #1, Duct Burner OFF, Run average` | `sulfur_dioxide` |
| `SO2, EGT Turbine #1, Duct Burner ON` | `sulfur_dioxide` |
| `SO2, EGT Turbine #1, Duct Burner ON, Run average` | `sulfur_dioxide` |
| `SO2, EGT Turbine #3, Duct Burner OFF` | `sulfur_dioxide` |
| `SO2, EGT Turbine #3, Duct Burner OFF, Run average` | `sulfur_dioxide` |
| `SO2, EGT Turbine #3, Duct Burner ON` | `sulfur_dioxide` |
| `SO2, EGT Turbine #3, Duct Burner ON, Run average` | `sulfur_dioxide` |
| `SO2, EUTURBINE/DB1` | `sulfur_dioxide` |
| `SO2, EUTURBINE/DB2` | `sulfur_dioxide` |
| `SO2, EUTURBINE/DB3` | `sulfur_dioxide` |
| `SO2, EUTURBINE4` | `sulfur_dioxide` |
| `SO2, Run 1, Solar Turbine 4` | `sulfur_dioxide` |
| `SO2, Run 2, Solar Turbine 4` | `sulfur_dioxide` |
| `SO2, Run 3, Solar Turbine 4` | `sulfur_dioxide` |
| `SO2, Solar Turbine GT4 (0.41 lb/hr limit), Run Avg` | `sulfur_dioxide` |
| `SO2, Turbine 1 only mode, three-test average` | `sulfur_dioxide` |
| `SO2, Turbine 1 only mode, three-test average, exceeds permit limit of 2.9 lb/hr` | `sulfur_dioxide` |
| `SO2, Turbine 2 only mode, three-test average` | `sulfur_dioxide` |
| `SO2, Turbine 2 only mode, three-test average, exceeds permit limit of 2.9 lb/hr` | `sulfur_dioxide` |
| `SO2, Turbine 3 only mode, three-test average` | `sulfur_dioxide` |
| `SO2, Turbine 3 only mode, three-test average, exceeds permit limit of 2.9 lb/hr` | `sulfur_dioxide` |
| `SO2, Turbine 4, three-test average, exceeds permit limit of 0.9 lb/MWhr` | `sulfur_dioxide` |
| `SO2, multiple turbines, Run Avg` | `sulfur_dioxide` |
| `SO2, multiple units` | `sulfur_dioxide` |
| `SO2, three-test average, EXCEEDED permit limit of 0.15` | `sulfur_dioxide` |
| `SO2, three-test average, EXCEEDED permit limit of 0.9` | `sulfur_dioxide` |
| `SOx 12-month limit` | `sulfur_dioxide` |
| `SOx emissions` | `sulfur_dioxide` |
| `SO₂ @ 15% O₂, Stack testing Run No. 1` | `sulfur_dioxide` |
| `SO₂ NSPS KKKK limit` | `sulfur_dioxide` |
| `SO₂ annual permit limit for each FGDUCTBURNERS-S3` | `sulfur_dioxide` |
| `SO₂ annual permit limit for each FGTURBINES-S3` | `sulfur_dioxide` |
| `SO₂ calculated from Method 6C measurement` | `sulfur_dioxide` |
| `SO₂ calculated from fuel analysis (H₂S at 125.76 ppm)` | `sulfur_dioxide` |
| `SO₂ emission limit for EUDUCTBURNER1-S3 and EUDUCTBURNER3-S3` | `sulfur_dioxide` |
| `SO₂ emission limit for EUOPENFLARE_TEMP` | `sulfur_dioxide` |
| `SO₂ emission limit for EUTURBINE1-S3 and EUTURBINE3-S3` | `sulfur_dioxide` |
| `SO₂ emission rate, Solar Turbine No. 4, PTI 274-03A` | `sulfur_dioxide` |
| `SO₂ emission rate, Solar Turbine No. 4, PTI 274-03B` | `sulfur_dioxide` |
| `SO₂ emissions rate, Run No. 1` | `sulfur_dioxide` |
| `SO₂ from Duct Burner 1 & 3 (FGDUCTBURNERS-S3), stack test Oct 16–19, 2018; limit 0.3 lb/hr` | `sulfur_dioxide` |
| `SO₂ from Duct Burner 2 (FGDUCTBURNERS-S3), stack test Oct 16–19, 2018; limit 0.3 lb/hr` | `sulfur_dioxide` |
| `SO₂ from EUTURBINE1-S3, DEQ calculated` | `sulfur_dioxide` |
| `SO₂ from EUTURBINE3-S3, DEQ calculated` | `sulfur_dioxide` |
| `SO₂ from EUTURBINE4-S3 (Solar Taurus), stack test Oct 19, 2018; limit 0.9 lb/MW hr` | `sulfur_dioxide` |
| `SO₂ from Turbine 1 (FGTURBINES-S3), stack test Oct 16–19, 2018; limit 2.9 lb/hr` | `sulfur_dioxide` |
| `SO₂ from Turbine 2 (FGTURBINES-S3), stack test Oct 16–19, 2018; limit 2.9 lb/hr` | `sulfur_dioxide` |
| `SO₂ from Turbine 3 (FGTURBINES-S3), stack test Oct 16–19, 2018; limit 2.9 lb/hr` | `sulfur_dioxide` |
| `SO₂ measured at Turbine 1 with Duct Burner by Method 6C` | `sulfur_dioxide` |
| `SO₂ permit limit for EUTURBINE4-S3` | `sulfur_dioxide` |
| `SO₂ permit limit for each FGDUCTBURNERS-S3` | `sulfur_dioxide` |
| `SO₂ permit limit for each FGTURBINES-S3 (Turbines 1–3)` | `sulfur_dioxide` |
| `STS vessel relief valve design lifting pressure` | `pressure_vacuum` |
| `Salem Twp WWTP authorized continuous discharge flow` | `flow_wastewater` |
| `Scentometer reading maximum for the day` | `wind_odor` |
| `Screen submerged By Sediment/Obstruction Q1 2024` | `event_status` |
| `Screen submerged Q1 2024` | `event_status` |
| `Screen submersion threshold - wells with >50% screen submerged by liquid or obstruction trigger corrective action consideration` | `other` |
| `Selenium` | `selenium` |
| `Selenium - measured data point` | `selenium` |
| `Selenium - recommended monthly limit` | `selenium` |
| `Selenium PEL (FCV monthly avg; WQS)` | `selenium` |
| `Selenium PEQ` | `selenium` |
| `Selenium at MP001A effluent, less-than value` | `selenium` |
| `Selenium discharge limit` | `selenium` |
| `Selenium in influent; WQS limit 5 ug/L, exceeds by 6x` | `selenium` |
| `Selenium in influent; exceeds WQS by 6x` | `selenium` |
| `Selenium measured in facility effluent` | `selenium` |
| `Selenium quantification level required; historically exceeded` | `selenium` |
| `Selenium recommended monthly limit` | `selenium` |
| `Selenium, East Pond, above permit limit` | `selenium` |
| `Selenium, West Pond, above permit limit` | `selenium` |
| `Shortfall range: between 7,000 and 10,000 scfm in backup flaring capacity` | `operational_capacity` |
| `Significant methane leak at vent nipple, STS; H2S odors present` | `methane_secondary` |
| `Significant net emissions increase threshold for SO2` | `sulfur_dioxide` |
| `Silver, Total` | `other` |
| `Site-specific mercury monitoring data point` | `mercury` |
| `Smoke/burning odor, Napier Rd / Florissant Dr downwind area, 8:54 PM–9:08 PM` | `wind_odor` |
| `Solar GT#4 Run 1 NOx corrected to 15% O2 preliminary result` | `nitrogen_oxides` |
| `Solar GT#4 Run 1 NOx preliminary result` | `nitrogen_oxides` |
| `Solar GT#4 Run 2 NOx preliminary result` | `nitrogen_oxides` |
| `Solar Taurus model 60 gas turbine electrical capacity` | `operational_capacity` |
| `Solid pipe length threshold - wells with >30 feet of solid pipe without open screen may require replacement` | `other` |
| `Stipulated penalty amount per Consent Judgment CJ No. 2020-0593-CE Paragraph 13.4 ($750/day for 2 days of noncompliance)` | `other` |
| `Stipulated penalty per day for violation under CJ 13.4` | `event_status` |
| `Storage tank capacity, aeration and carbon filtration required by CJ` | `operational_capacity` |
| `Stormwater pond west of leachate tanks; pumped to date` | `flow_wastewater` |
| `Stormwater removed from pond west of leachate tanks as of Jan 4, 2017` | `flow_wastewater` |
| `Subpart WWW methane background threshold requiring correction` | `methane_secondary` |
| `Sulfur Dioxide` | `sulfur_dioxide` |
| `Sulfur Dioxide (SO2)` | `sulfur_dioxide` |
| `Sulfur Dioxide limit for EUTURBINE1, EUTURBINE2, EUTURBINE3` | `sulfur_dioxide` |
| `Sulfur Dioxide limit for EUTURBINE4` | `sulfur_dioxide` |
| `Sulfur Run #2, EGT1` | `trs` |
| `Sulfur content by weight from MAERS report (converts to ~300–400 ppm)` | `trs` |
| `Sulfur content limit by weight for landfill gas` | `trs` |
| `Sulfur content of LFG sample from final test results` | `other` |
| `Sulfur content of fuel maximum limit` | `other` |
| `Sulfur dioxide (SO2) limit for turbines under Michigan Air Pollution Control Rule` | `sulfur_dioxide` |
| `Sulfur dioxide emission limit` | `sulfur_dioxide` |
| `Sulfur dioxide emission limit (12-month rolling)` | `sulfur_dioxide` |
| `Sulfur dioxide limit` | `sulfur_dioxide` |
| `Sulfur dioxide limit, FGENCLOSEDFLARES-S2` | `sulfur_dioxide` |
| `Sulfur measurement at gas plant, stable from previous readings` | `trs` |
| `Surface emissions exceedance (Penetration)` | `surface_emissions` |
| `Surface emissions exceedance (Penetration) at cleanout location` | `surface_emissions` |
| `Surface emissions exceedance (Penetration) at sump location` | `surface_emissions` |
| `Surface emissions exceedance (Penetration) at ~50 ft N of WW-266` | `surface_emissions` |
| `Surface emissions exceedance (Penetration), initial reading at unmarked location ~30 ft S of EW-15R2` | `surface_emissions` |
| `Surface emissions exceedance (SEM) at ~10 ft E of TS-02` | `surface_emissions` |
| `Surface emissions exceedance (SEM) at ~20 ft N of EW-54` | `surface_emissions` |
| `Surface emissions exceedance (SEM) at ~20 ft N of WW-264` | `surface_emissions` |
| `Surface emissions exceedance (SEM) at ~20 ft NE of ECS-9` | `surface_emissions` |
| `Surface emissions exceedance (SEM) at ~200 ft SE of WW-439R` | `surface_emissions` |
| `Surface emissions exceedance (SEM) at ~40 ft S of WW-261R` | `surface_emissions` |
| `Surface emissions exceedance (SEM) at ~57 ft W of WW-450` | `surface_emissions` |
| `Surface emissions exceedance (SEM) at ~70 ft E of M#3` | `surface_emissions` |
| `Surface emissions exceedance (SEM) at ~81 ft N of WW-336` | `surface_emissions` |
| `Surface emissions exceedance (SEM), manhole location` | `surface_emissions` |
| `Surface emissions monitoring locations exceeding 500 ppm methane in Q1 2025` | `exceedances_count` |
| `Surface emissions monitoring locations exceeding 500 ppm methane in Q2 2025` | `exceedances_count` |
| `Surface landfill scan hits in 4th quarter 2018, dramatically worse than previous quarters` | `methane` |
| `Surface methane concentration limit above background; ROP and NSPS standard` | `methane` |
| `Surface methane monitoring exceedances above 500 ppm in Q2 2021, all cleared within quarter` | `exceedances_count` |
| `Surface scan remediation threshold` | `surface_emissions` |
| `Surface water elevation difference between Wetland 1 and Pond 3 (May 2026 survey)` | `other` |
| `Synthetic cover (GCL 40 mil composite) tarped at south end of landfill protective cover` | `other` |
| `TOC (Total Organic Carbon)` | `toc` |
| `TOC, ONYX-001 sample` | `toc` |
| `TOC, ONYX-001-1 sample` | `toc` |
| `TOC, ONYX-001-2 sample` | `toc` |
| `TOC, ONYX-COMPOST sample` | `toc` |
| `TRS canister sampling` | `trs` |
| `TS-01 pumping rate vs. previous estimate of 10,000 gpd` | `flow_wastewater` |
| `TSS (Total Suspended Solids) at effluent monitoring point 001A` | `tss` |
| `TSS (Total Suspended Solids); exceeds permit limit of 30 mg/L monthly average` | `tss` |
| `TSS (Total Suspended Solids); exceeds permit limit of 38 lbs/day daily maximum` | `tss` |
| `TSS (Total Suspended Solids); permit limit 30 mg/L monthly average` | `tss` |
| `TSS Maximum Daily permit limit` | `tss` |
| `TSS Maximum Monthly Average permit limit` | `tss` |
| `TSS at effluent monitoring point 001A, below detection` | `tss` |
| `TSS average` | `tss` |
| `TSS calculated monthly average concentration (April 2021)` | `tss` |
| `TSS concentration sample 1` | `tss` |
| `TSS concentration sample 2` | `tss` |
| `TSS concentration sample 3` | `tss` |
| `TSS daily maximum loading` | `tss` |
| `TSS daily maximum loading permitted limit` | `tss` |
| `TSS daily maximum measured during pond drainage` | `tss` |
| `TSS daily maximum permit limit` | `tss` |
| `TSS daily maximum permitted limit` | `tss` |
| `TSS daily maximum; permit limit 30 mg/L` | `tss` |
| `TSS daily maximum; permit limit 38 lbs/day` | `tss` |
| `TSS effluent concentration` | `tss` |
| `TSS maximum` | `tss` |
| `TSS maximum daily limit` | `tss` |
| `TSS monthly average concentration` | `tss` |
| `TSS monthly average concentration permitted limit` | `tss` |
| `TSS monthly average permit limit` | `tss` |
| `TSS permit requirement maximum daily` | `tss` |
| `TSS permit requirement maximum monthly average` | `tss` |
| `TSS resample due to sediment intrusion` | `tss` |
| `TSS-Max Daily at MP001A effluent` | `tss` |
| `TSS; exceeds permit limit of 30 mg/L monthly average` | `tss` |
| `TSS; exceeds permit limit of 38 lbs/day daily maximum` | `tss` |
| `TSS; permit limit 38 lbs/day daily maximum` | `tss` |
| `Temperature (Winter)` | `temperature_secondary` |
| `Temperature Winter` | `temperature_secondary` |
| `Temperature threshold for WOI well designation per May 6, 2019 HOV Approval Letter` | `temperature` |
| `Temporary Flare gas flow rate` | `operational_capacity` |
| `Temporary candlestick flare rated capacity (North side, installed March 2017)` | `operational_capacity` |
| `Temporary cap area on lower northwest slope` | `other` |
| `Temporary cap area on lower northwest slope with supplemented 2 feet of clay soils` | `event_status` |
| `Temporary flare flow rate at 1:20 pm` | `operational_capacity` |
| `Temporary low vacuum during DTE outage blower programming problem (4-hour period)` | `pressure_vacuum` |
| `Temporary open flare capacity` | `operational_capacity` |
| `Thermal Discharge - Average` | `temperature_secondary` |
| `Thermal Discharge - Maximum` | `temperature_secondary` |
| `Thermal Discharge - Monthly Average Limit` | `temperature_secondary` |
| `Thermal Discharge maximum monthly average` | `other` |
| `Thermal Discharge monthly average limit` | `temperature_secondary` |
| `Thermal Discharge, Maximum Monthly Average` | `other` |
| `Thermal discharge maximum (March)` | `other` |
| `Thermal oxidizer minimum retention time` | `other` |
| `Thermal oxidizer minimum temperature` | `other` |
| `Third-party odor surveillance reading at community locations in Steeplechase Subdivision during 5 PM-8 PM window` | `surface_emissions` |
| `Third-party odor surveillance reading; less than 2 SEM during inspections` | `surface_emissions` |
| `This value appears in the schema instructions as an example but is NOT present in the actual document text; included per schema requirement but document contains no temperature readings` | `other` |
| `Three flares combined capacity` | `operational_capacity` |
| `Threshold below which quarterly monitoring may be requested` | `mercury` |
| `Toluene (below detection limit)` | `btex_chlorinated_voc` |
| `Total Antimony` | `antimony` |
| `Total Arbor Hills West leachate, August 2024` | `flow_wastewater` |
| `Total Arbor Hills West leachate, July 2024` | `flow_wastewater` |
| `Total Arbor Hills West leachate, September 2024` | `flow_wastewater` |
| `Total Arsenic` | `arsenic` |
| `Total Arsenic (Maximum)` | `arsenic` |
| `Total Arsenic - Average` | `arsenic` |
| `Total Arsenic - Maximum` | `arsenic` |
| `Total Arsenic - Maximum Monthly Average` | `arsenic` |
| `Total Arsenic - average` | `arsenic` |
| `Total Arsenic - concentration` | `arsenic` |
| `Total Arsenic - maximum` | `arsenic` |
| `Total Arsenic Maximum Monthly Average` | `arsenic` |
| `Total Arsenic Maximum Monthly Average (concentration)` | `arsenic` |
| `Total Arsenic average` | `arsenic` |
| `Total Arsenic average concentration` | `arsenic` |
| `Total Arsenic concentration` | `arsenic` |
| `Total Arsenic concentration maximum` | `arsenic` |
| `Total Arsenic concentration maximum monthly average` | `arsenic` |
| `Total Arsenic concentration minimum` | `arsenic` |
| `Total Arsenic maximum` | `arsenic` |
| `Total Arsenic maximum concentration` | `arsenic` |
| `Total Arsenic maximum measured` | `arsenic` |
| `Total Arsenic maximum monthly average` | `arsenic` |
| `Total Arsenic maximum monthly average concentration` | `arsenic` |
| `Total Arsenic monthly average limit` | `arsenic` |
| `Total Arsenic permit maximum monthly average` | `arsenic` |
| `Total Arsenic, Final Effluent` | `arsenic` |
| `Total Arsenic, Maximum Monthly Average` | `arsenic` |
| `Total Arsenic, average` | `arsenic` |
| `Total Arsenic, average concentration` | `arsenic` |
| `Total Arsenic, maximum` | `arsenic` |
| `Total Arsenic, maximum concentration` | `arsenic` |
| `Total BETX discharge limit` | `btex_chlorinated_voc` |
| `Total BETX discharge limitation` | `btex_chlorinated_voc` |
| `Total BTEX discharge limit` | `btex_chlorinated_voc` |
| `Total Cadmium` | `cadmium` |
| `Total Chromium` | `chromium` |
| `Total Copper` | `copper` |
| `Total Daily Flow average` | `flow_wastewater` |
| `Total Daily Flow average during discharge period` | `flow_wastewater` |
| `Total Daily Flow maximum` | `flow_wastewater` |
| `Total Daily Flow maximum during discharge period` | `flow_wastewater` |
| `Total Dissolved Solids (TDS) - Influent Pond` | `tds` |
| `Total Lead` | `lead` |
| `Total Mercury` | `mercury` |
| `Total Mercury (71900) - 12-Month Rolling Average` | `mercury` |
| `Total Mercury (71900) - 12-Month Rolling Average permit requirement` | `mercury` |
| `Total Mercury (Hg Calculation)` | `mercury` |
| `Total Mercury (Hg Calculation) - 12-Month Rolling Average` | `mercury` |
| `Total Mercury (Hg Calculation) concentration` | `mercury` |
| `Total Mercury - 12-Month Rolling Average` | `mercury` |
| `Total Mercury - 12-Month Rolling Average Limit` | `mercury` |
| `Total Mercury - 12-Month Rolling Average limit` | `mercury` |
| `Total Mercury - Average and Maximum` | `mercury` |
| `Total Mercury - Final Effluent (1)` | `mercury` |
| `Total Mercury - Hg Calculation` | `mercury` |
| `Total Mercury - January quarterly calculation` | `mercury` |
| `Total Mercury - average` | `mercury` |
| `Total Mercury - concentration` | `mercury` |
| `Total Mercury - field duplicate` | `mercury` |
| `Total Mercury - maximum measured sample` | `mercury` |
| `Total Mercury - recommended monthly limit (Level Currently Achievable)` | `mercury` |
| `Total Mercury 12-Month Rolling Average` | `mercury` |
| `Total Mercury 12-Month Rolling Average (concentration)` | `mercury` |
| `Total Mercury 12-Month Rolling Average limit` | `mercury` |
| `Total Mercury 12-Month Rolling Average permit limit` | `mercury` |
| `Total Mercury 12-Month Rolling Average permitted limit` | `mercury` |
| `Total Mercury 12-month rolling average` | `mercury` |
| `Total Mercury 12-month rolling average concentration` | `mercury` |
| `Total Mercury 12-month rolling average limit` | `mercury` |
| `Total Mercury 12-month rolling average limit (loading)` | `mercury` |
| `Total Mercury 12-month rolling average loading limit` | `mercury` |
| `Total Mercury 12-month rolling average maximum` | `mercury` |
| `Total Mercury 12-month rolling average permit requirement` | `mercury` |
| `Total Mercury Hg Calculation` | `mercury` |
| `Total Mercury Hg Calculation - concentration` | `mercury` |
| `Total Mercury Hg calculation` | `mercury` |
| `Total Mercury average and maximum` | `mercury` |
| `Total Mercury concentration` | `mercury` |
| `Total Mercury final effluent limitation` | `mercury` |
| `Total Mercury maximum measured concentration January 2008 to July 2008` | `mercury` |
| `Total Mercury measured` | `mercury` |
| `Total Mercury measurement` | `mercury` |
| `Total Mercury recommended limit` | `mercury` |
| `Total Mercury water quality-based effluent limit (baseline before variance)` | `mercury` |
| `Total Mercury, 12-Month Rolling Average` | `mercury` |
| `Total Mercury, 12-month rolling average limit` | `mercury` |
| `Total Mercury, Final Effluent` | `mercury` |
| `Total Mercury, Final Effluent (1)` | `mercury` |
| `Total Mercury, Final Effluent (1); permit (Report)` | `mercury` |
| `Total Mercury, concentration` | `mercury` |
| `Total Mercury, monthly calculation` | `mercury` |
| `Total Mercury—12-Month Rolling Average` | `mercury` |
| `Total NOx emission limit for facility-wide system` | `nitrogen_oxides` |
| `Total NOx emission rate 12-month rolling period limit` | `nitrogen_oxides` |
| `Total NOx emission rate limit for 12-month rolling period` | `nitrogen_oxides` |
| `Total Nickel` | `nickel` |
| `Total Nickel - Maximum Monthly Average` | `nickel` |
| `Total Nickel Maximum Monthly Average` | `nickel` |
| `Total Nickel Maximum Monthly Average (concentration)` | `nickel` |
| `Total Nickel average` | `nickel` |
| `Total Nickel average concentration` | `nickel` |
| `Total Nickel concentration maximum monthly average` | `nickel` |
| `Total Nickel maximum` | `nickel` |
| `Total Nickel maximum concentration` | `nickel` |
| `Total Nickel maximum monthly average` | `nickel` |
| `Total Nickel maximum monthly average concentration` | `nickel` |
| `Total Nickel monthly average limit` | `nickel` |
| `Total Nickel, Maximum Monthly Average` | `nickel` |
| `Total Organic Carbon (TOC)` | `toc` |
| `Total Organic Carbon, DAF Effluent sample CV09033` | `toc` |
| `Total Organic Carbon, Mid GAC sample CV09034` | `toc` |
| `Total PCB maximum concentration in AHE raw leachate (PCB-1232, PCB-1242, PCB-1248, PCB-1254)` | `other` |
| `Total PFAS grab sample 001A` | `pfas` |
| `Total Phenol in Discharge 001A, not detected above 10.0 limit` | `other` |
| `Total Phenolics in leachate effluent` | `other` |
| `Total Phosphorous (as P) max monthly` | `phosphorus` |
| `Total Phosphorous daily maximum` | `phosphorus` |
| `Total Phosphorus` | `phosphorus` |
| `Total Phosphorus (Maximum)` | `phosphorus` |
| `Total Phosphorus (as P)` | `phosphorus` |
| `Total Phosphorus (as P) (00665) - Maximum Monthly Average permit requirement` | `phosphorus` |
| `Total Phosphorus (as P) - Final Effluent (1)` | `phosphorus` |
| `Total Phosphorus (as P) - Maximum Monthly Average` | `phosphorus` |
| `Total Phosphorus (as P), Final Effluent (1)` | `phosphorus` |
| `Total Phosphorus (as P), Final Effluent (1); permit max (Report)` | `phosphorus` |
| `Total Phosphorus (as P), maximum monthly average limit` | `phosphorus` |
| `Total Phosphorus (as P); maximum daily` | `phosphorus` |
| `Total Phosphorus (as P); monthly average` | `phosphorus` |
| `Total Phosphorus (monthly average), January 2005, Outfall 001; exceeded limit of 0.8 lbs/day` | `phosphorus` |
| `Total Phosphorus (monthly average), January 2005, Outfall 001; exceeded limit of 1.0 mg/L` | `phosphorus` |
| `Total Phosphorus - Maximum Monthly Average` | `phosphorus` |
| `Total Phosphorus - Maximum Monthly Average limit` | `phosphorus` |
| `Total Phosphorus - Monthly Average Limit` | `phosphorus` |
| `Total Phosphorus - average` | `phosphorus` |
| `Total Phosphorus - less than detection` | `phosphorus` |
| `Total Phosphorus - less than value` | `phosphorus` |
| `Total Phosphorus - maximum` | `phosphorus` |
| `Total Phosphorus Maximum Monthly Average` | `phosphorus` |
| `Total Phosphorus Maximum Monthly Average limit` | `phosphorus` |
| `Total Phosphorus Maximum Monthly Average permitted limit` | `phosphorus` |
| `Total Phosphorus as P (00665) - Maximum Monthly Average` | `phosphorus` |
| `Total Phosphorus average` | `phosphorus` |
| `Total Phosphorus average concentration` | `phosphorus` |
| `Total Phosphorus concentration minimum and maximum` | `phosphorus` |
| `Total Phosphorus daily maximum` | `phosphorus` |
| `Total Phosphorus maximum` | `phosphorus` |
| `Total Phosphorus maximum concentration` | `phosphorus` |
| `Total Phosphorus maximum monthly average` | `phosphorus` |
| `Total Phosphorus maximum monthly average concentration` | `phosphorus` |
| `Total Phosphorus maximum monthly average limit` | `phosphorus` |
| `Total Phosphorus maximum monthly average limit (loading)` | `phosphorus` |
| `Total Phosphorus maximum; permitted limit 1.0 mg/L monthly average` | `phosphorus` |
| `Total Phosphorus monthly limit` | `phosphorus` |
| `Total Phosphorus permit maximum monthly average` | `phosphorus` |
| `Total Phosphorus permit requirement maximum monthly average` | `phosphorus` |
| `Total Phosphorus, Maximum Monthly Average` | `phosphorus` |
| `Total Phosphorus, permit limit 1.0 mg/l` | `phosphorus` |
| `Total Phosphorus, permit limit REPORT` | `phosphorus` |
| `Total Phosphorus-P, Sample AH-FC` | `phosphorus` |
| `Total Phosphorus-P, Sample AH-GC` | `phosphorus` |
| `Total Phosphorus; permit requires reporting` | `phosphorus` |
| `Total Phosphorus—Maximum Monthly Average` | `phosphorus` |
| `Total Reduced Sulfur (H2S) concentration limit in landfill gas for FGPROJECT23` | `trs` |
| `Total Reduced Sulfur (TRS) concentration as H2S in landfill gas, hourly limit` | `trs` |
| `Total Reduced Sulfur and H2S Utility Flare semi-annual` | `trs` |
| `Total Reduced Sulfur concentration limit in landfill gas for FGPROJECT23` | `trs` |
| `Total Selenium` | `selenium` |
| `Total Selenium - Maximum Monthly Average` | `selenium` |
| `Total Selenium - average` | `selenium` |
| `Total Selenium - less than detection` | `selenium` |
| `Total Selenium - less than value` | `selenium` |
| `Total Selenium - maximum` | `selenium` |
| `Total Selenium 12-Month Rolling Average` | `selenium` |
| `Total Selenium 12-Month Rolling Average concentration` | `selenium` |
| `Total Selenium Maximum Monthly Average` | `selenium` |
| `Total Selenium Maximum Monthly Average (concentration)` | `selenium` |
| `Total Selenium average` | `selenium` |
| `Total Selenium concentration` | `selenium` |
| `Total Selenium concentration maximum monthly average` | `selenium` |
| `Total Selenium limit` | `selenium` |
| `Total Selenium maximum` | `selenium` |
| `Total Selenium maximum measured` | `selenium` |
| `Total Selenium maximum monthly average` | `selenium` |
| `Total Selenium maximum monthly average concentration` | `selenium` |
| `Total Selenium measurement` | `selenium` |
| `Total Selenium monthly average limit` | `selenium` |
| `Total Selenium permit maximum monthly average` | `selenium` |
| `Total Selenium permit requirement` | `selenium` |
| `Total Selenium, April 2014, Outfall 001` | `selenium` |
| `Total Selenium, Maximum Monthly Average` | `selenium` |
| `Total Selenium, average` | `selenium` |
| `Total Selenium, maximum` | `selenium` |
| `Total Solids permit limit, maximum month` | `tss` |
| `Total Solids permit limit, maximum month concentration` | `tss` |
| `Total Solids, Outfall 001A, concentration` | `tss` |
| `Total Solids, Outfall 001A, loading` | `tss` |
| `Total Sulfur as H2S, canister sampling` | `trs` |
| `Total Sulfur in LFG sample` | `trs` |
| `Total Sulfur in LFG sample (canister sampling, lab analysis)` | `trs` |
| `Total Suspended Solids` | `tss` |
| `Total Suspended Solids (00530) - Maximum Daily` | `tss` |
| `Total Suspended Solids (00530) - Maximum Daily permit requirement` | `tss` |
| `Total Suspended Solids (00530) - Maximum Monthly Average` | `tss` |
| `Total Suspended Solids (00530) - Maximum Monthly Average permit requirement` | `tss` |
| `Total Suspended Solids (Average)` | `tss` |
| `Total Suspended Solids (Maximum)` | `tss` |
| `Total Suspended Solids (TSS)` | `tss` |
| `Total Suspended Solids (TSS) daily maximum, December-April` | `tss` |
| `Total Suspended Solids (TSS) daily maximum, May 1 – Sept. 30` | `tss` |
| `Total Suspended Solids (TSS) daily maximum, May-November` | `tss` |
| `Total Suspended Solids (TSS) daily maximum, Oct. 1 – April 30` | `tss` |
| `Total Suspended Solids (TSS) maximum daily` | `tss` |
| `Total Suspended Solids (TSS) maximum monthly average` | `tss` |
| `Total Suspended Solids (TSS) monthly maximum, May 1 – Sept. 30` | `tss` |
| `Total Suspended Solids (TSS) monthly maximum, Oct. 1 – April 30` | `tss` |
| `Total Suspended Solids (TSS), average` | `tss` |
| `Total Suspended Solids (TSS), average concentration` | `tss` |
| `Total Suspended Solids (TSS), below detection limit <10` | `tss` |
| `Total Suspended Solids (TSS), maximum` | `tss` |
| `Total Suspended Solids (TSS), maximum concentration` | `tss` |
| `Total Suspended Solids (TSS), maximum daily limit` | `tss` |
| `Total Suspended Solids (TSS), maximum monthly average limit` | `tss` |
| `Total Suspended Solids - Average` | `tss` |
| `Total Suspended Solids - Average concentration` | `tss` |
| `Total Suspended Solids - Daily Maximum Limit` | `tss` |
| `Total Suspended Solids - Final Effluent (1)` | `tss` |
| `Total Suspended Solids - Maximum` | `tss` |
| `Total Suspended Solids - Maximum Daily` | `tss` |
| `Total Suspended Solids - Maximum Daily limit` | `tss` |
| `Total Suspended Solids - Maximum Monthly Average` | `tss` |
| `Total Suspended Solids - Maximum Monthly Average limit` | `tss` |
| `Total Suspended Solids - Maximum concentration` | `tss` |
| `Total Suspended Solids - Monthly Average Limit` | `tss` |
| `Total Suspended Solids - average` | `tss` |
| `Total Suspended Solids - below detection limit (< 10)` | `tss` |
| `Total Suspended Solids - maximum` | `tss` |
| `Total Suspended Solids Maximum Daily` | `tss` |
| `Total Suspended Solids Maximum Daily limit` | `tss` |
| `Total Suspended Solids Maximum Daily permit limit` | `tss` |
| `Total Suspended Solids Maximum Daily permitted limit` | `tss` |
| `Total Suspended Solids Maximum Monthly Average` | `tss` |
| `Total Suspended Solids Maximum Monthly Average permitted limit` | `tss` |
| `Total Suspended Solids May-November daily maximum` | `tss` |
| `Total Suspended Solids average` | `tss` |
| `Total Suspended Solids average (CBOD5)` | `tss` |
| `Total Suspended Solids average concentration` | `tss` |
| `Total Suspended Solids daily maximum limit` | `tss` |
| `Total Suspended Solids in Outfall 001A Composite` | `tss` |
| `Total Suspended Solids in Outfall 001A composite wastewater; <10 (below RDL)` | `tss` |
| `Total Suspended Solids max daily` | `tss` |
| `Total Suspended Solids maximum` | `tss` |
| `Total Suspended Solids maximum (CBOD5)` | `tss` |
| `Total Suspended Solids maximum concentration` | `tss` |
| `Total Suspended Solids maximum daily` | `tss` |
| `Total Suspended Solids maximum daily (permit limit 38 lbs/day)` | `tss` |
| `Total Suspended Solids maximum daily concentration` | `tss` |
| `Total Suspended Solids maximum daily limit` | `tss` |
| `Total Suspended Solids maximum daily limit (loading)` | `tss` |
| `Total Suspended Solids maximum daily loading limit` | `tss` |
| `Total Suspended Solids maximum daily loading; permit limit 38 lbs/day` | `tss` |
| `Total Suspended Solids maximum daily; permit limit 45 mg/L` | `tss` |
| `Total Suspended Solids maximum monthly average` | `tss` |
| `Total Suspended Solids maximum monthly average concentration` | `tss` |
| `Total Suspended Solids maximum monthly average limit` | `tss` |
| `Total Suspended Solids maximum; permitted limit 45 mg/L` | `tss` |
| `Total Suspended Solids monthly average, April 2021` | `tss` |
| `Total Suspended Solids permit maximum daily` | `tss` |
| `Total Suspended Solids permit maximum monthly average` | `tss` |
| `Total Suspended Solids permit requirement` | `tss` |
| `Total Suspended Solids, DAF Effluent sample CV09033` | `tss` |
| `Total Suspended Solids, Final Effluent` | `tss` |
| `Total Suspended Solids, Final Effluent (1); permit max 45 mg/L` | `tss` |
| `Total Suspended Solids, Jan–Apr, Dec maximum daily` | `tss` |
| `Total Suspended Solids, Jan–Apr, Dec maximum monthly average` | `tss` |
| `Total Suspended Solids, Maximum Daily` | `tss` |
| `Total Suspended Solids, Maximum Monthly Average` | `tss` |
| `Total Suspended Solids, May–Nov maximum daily` | `tss` |
| `Total Suspended Solids, May–Nov maximum monthly average` | `tss` |
| `Total Suspended Solids, Mid GAC sample CV09034` | `tss` |
| `Total Suspended Solids, Outfall 001A Composite` | `tss` |
| `Total Suspended Solids, Sample AH-FC` | `tss` |
| `Total Suspended Solids, Sample AH-GC` | `tss` |
| `Total Suspended Solids, West Pond sample` | `tss` |
| `Total Suspended Solids, average` | `tss` |
| `Total Suspended Solids, maximum` | `tss` |
| `Total Suspended Solids, permit limit 25 lbs/day` | `tss` |
| `Total Suspended Solids, permit limit 30 mg/l` | `tss` |
| `Total Suspended Solids, permit limit 45 mg/L` | `tss` |
| `Total Suspended Solids, sediment intrusion resample` | `tss` |
| `Total Suspended Solids; maximum daily` | `tss` |
| `Total Suspended Solids; monthly average` | `tss` |
| `Total Suspended Solids—Maximum Daily` | `tss` |
| `Total Suspended Solids—Maximum Monthly Average` | `tss` |
| `Total VOC emission limit` | `nmoc_voc` |
| `Total VOC emission limit (12-month rolling)` | `nmoc_voc` |
| `Total Zinc` | `zinc` |
| `Total arsenic` | `arsenic` |
| `Total arsenic in Outfall 001 maximum, April 2014 - March 2016` | `arsenic` |
| `Total arsenic in Outfall 001 minimum, April 2014 - March 2016` | `arsenic` |
| `Total arsenic in final effluent, maximum` | `arsenic` |
| `Total arsenic in final effluent, minimum` | `arsenic` |
| `Total arsenic maximum monthly average` | `arsenic` |
| `Total arsenic monthly concentration limit` | `arsenic` |
| `Total average landfill gas flow collected (April 2026)` | `operational_capacity` |
| `Total combined HAPs for all three flares` | `other` |
| `Total combined flaring capacity of 3 south-side flares as of January 2019 inspection due to blower over-amping` | `operational_capacity` |
| `Total diesel` | `other` |
| `Total gas collection wells with available liquid level data` | `well_operational` |
| `Total hardness from 2006 Compliance Sampling Inspection` | `hardness` |
| `Total interim backup capacity with all short-term fixes in place` | `operational_capacity` |
| `Total landfill gas combusted` | `operational_capacity` |
| `Total landfill gas flow` | `operational_capacity` |
| `Total liquid level data available for September 2018` | `well_operational` |
| `Total mercury` | `mercury` |
| `Total mercury 12-month rolling average` | `mercury` |
| `Total mercury 12-month rolling average limit` | `mercury` |
| `Total mercury Discharge Specific Level Currently Achievable (LCA)` | `mercury` |
| `Total mercury LCA (Level Currently Achievable) final effluent limit` | `mercury` |
| `Total mercury NPDES effluent limit` | `mercury` |
| `Total mercury concentration limit` | `mercury` |
| `Total mercury discharge-specific level currently achievable (LCA)` | `mercury` |
| `Total mercury discharge-specific level currently achievable (LCA), 12-month rolling average` | `mercury` |
| `Total mercury effluent concentration limit in PMP` | `mercury` |
| `Total mercury effluent limitation` | `mercury` |
| `Total mercury final effluent limitation` | `mercury` |
| `Total mercury final effluent limitation (discharge-specific level via MDV)` | `mercury` |
| `Total mercury in Outfall 001 maximum, November 2011 - March 2016` | `mercury` |
| `Total mercury in Outfall 001 minimum, November 2011 - March 2016` | `mercury` |
| `Total mercury in final effluent, maximum` | `mercury` |
| `Total mercury in final effluent, maximum recorded April 2014–March 2016` | `mercury` |
| `Total mercury in final effluent, minimum` | `mercury` |
| `Total mercury in final effluent, minimum recorded April 2014–March 2016` | `mercury` |
| `Total mercury loading limit (12-month rolling average)` | `mercury` |
| `Total mercury regulatory limit (April 2008)` | `mercury` |
| `Total mercury regulatory limit (most samples)` | `mercury` |
| `Total mercury reporting limit` | `mercury` |
| `Total mercury water quality standard` | `mercury` |
| `Total mercury water quality standard / action level for Outfall 001` | `mercury` |
| `Total mercury, 12-month rolling average, Monitoring Point 001A / Outfall 001` | `mercury` |
| `Total mercury, 12-month rolling average, NPDES violation` | `mercury` |
| `Total mercury, NPDES Pond` | `mercury` |
| `Total mercury, NPDES Pond, resampled for verification` | `mercury` |
| `Total nickel in Outfall 001 maximum, April 2014 - March 2016` | `nickel` |
| `Total nickel in Outfall 001 minimum, April 2014 - March 2016` | `nickel` |
| `Total nickel maximum monthly average` | `nickel` |
| `Total nickel monthly concentration limit` | `nickel` |
| `Total odor readings by RK Associates from Nov 2016 to Aug 2018 at community and perimeter locations` | `wind_odor` |
| `Total organic carbon` | `toc` |
| `Total phenolics - non-detected (ND) at reporting limit, NPDES discharge sample Discharge 001A` | `other` |
| `Total phenolics, water discharge sample DISCHARGE 001A - GRAB` | `other` |
| `Total phenols reporting limit (RL); actual measured result: not detected` | `other` |
| `Total phosphorus` | `phosphorus` |
| `Total phosphorus average (less than)` | `phosphorus` |
| `Total phosphorus concentration` | `phosphorus` |
| `Total phosphorus in final effluent, maximum` | `phosphorus` |
| `Total phosphorus loading limit` | `phosphorus` |
| `Total phosphorus maximum (less than)` | `phosphorus` |
| `Total phosphorus maximum monthly` | `phosphorus` |
| `Total phosphorus maximum monthly average` | `phosphorus` |
| `Total phosphorus monthly average limit` | `phosphorus` |
| `Total phosphorus monthly average loading limit` | `phosphorus` |
| `Total phosphorus monthly concentration limit` | `phosphorus` |
| `Total phosphorus monthly maximum limit` | `phosphorus` |
| `Total phosphorus, permit limit 0.8 lbs/day` | `phosphorus` |
| `Total phosphorus, permit limit 1.0 mg/l` | `phosphorus` |
| `Total plant gas flow to turbines` | `operational_capacity` |
| `Total plant gas including flare and duct burners` | `operational_capacity` |
| `Total readings showing <7 dilutions across entire monitoring period` | `wind_odor` |
| `Total reduced sulfur (TRS) in treated LFG fuel` | `trs` |
| `Total reduced sulfur (TRS) in treated landfill gas` | `trs` |
| `Total reduced sulfur average of three samples` | `trs` |
| `Total reduced sulfur concentration limit in LFG measured as H2S` | `trs` |
| `Total reduced sulfur: first sample in 6-month collection` | `trs` |
| `Total reduced sulfur: second sample` | `trs` |
| `Total reduced sulfur: third sample` | `trs` |
| `Total selenium` | `selenium` |
| `Total selenium (less than)` | `selenium` |
| `Total selenium in Outfall 001 maximum, April 2014 - March 2016` | `selenium` |
| `Total selenium in Outfall 001 non-detect, April 2014 - March 2016` | `selenium` |
| `Total selenium in final effluent, maximum` | `selenium` |
| `Total selenium maximum monthly average` | `selenium` |
| `Total selenium weekly concentration limit` | `selenium` |
| `Total single HAP (HCl) for all three flares` | `hydrogen_chloride` |
| `Total solids, exceeded 25 lbs/day limit` | `tss` |
| `Total solids, exceeded 30 mg/L limit` | `tss` |
| `Total solids, marginally exceeded 25 lbs/day limit` | `tss` |
| `Total solids, nearly doubled 30 mg/L limit` | `tss` |
| `Total solids, permit limit 25 lbs/day` | `tss` |
| `Total solids, permit limit 30 mg/l` | `tss` |
| `Total sulfur concentration limit at STS outlet` | `trs` |
| `Total sulfur concentration limit at outlet of Sulfur Treatment System (EURNGPLANT)` | `trs` |
| `Total sulfur concentration limit at outlet of Sulfur Treatment System (STS)` | `trs` |
| `Total sulfur concentration limit at outlet of sulfur treatment system` | `trs` |
| `Total sulfur content in landfill gas per Jet-Care fuel analysis` | `other` |
| `Total suspended solids` | `tss` |
| `Total suspended solids (TSS) daily maximum concentration, AHRA east pond discharge` | `tss` |
| `Total suspended solids (TSS), sample collected April 28, 2015` | `tss` |
| `Total suspended solids (TSS), sample collected April 29, 2015` | `tss` |
| `Total suspended solids (TSS), sample collected April 30, 2015` | `tss` |
| `Total suspended solids concentration maximum daily` | `tss` |
| `Total suspended solids concentration maximum monthly average` | `tss` |
| `Total suspended solids maximum daily` | `tss` |
| `Total suspended solids maximum daily (Jan-May, Dec)` | `tss` |
| `Total suspended solids maximum daily (May-Nov)` | `tss` |
| `Total suspended solids maximum daily limit` | `tss` |
| `Total suspended solids maximum daily loading` | `tss` |
| `Total suspended solids maximum monthly (Jan-May, Dec)` | `tss` |
| `Total suspended solids maximum monthly (May-Nov)` | `tss` |
| `Total suspended solids maximum monthly average` | `tss` |
| `Total suspended solids, Outfall-001A Comp, below detection` | `tss` |
| `Total system NOx maximum 12-month rolling period` | `nitrogen_oxides` |
| `Total vacuum (negative)` | `pressure_vacuum` |
| `Trans-1,2-Dichloroethene max daily` | `btex_chlorinated_voc` |
| `Trans-1,2-dichloroethene daily discharge limitation` | `btex_chlorinated_voc` |
| `Trans-1,2-dichloroethene discharge limit` | `btex_chlorinated_voc` |
| `Trans-1,2-dichloroethene discharge limitation` | `btex_chlorinated_voc` |
| `Trans-1,2-dichloroethene limit` | `btex_chlorinated_voc` |
| `Trans-1,2-dichloroethylene maximum daily` | `btex_chlorinated_voc` |
| `Turbine 1 SO2 annual (8760 hrs/yr basis)` | `sulfur_dioxide` |
| `Turbine 1 SO2 annual emission rate (extrapolated from lb/hr)` | `sulfur_dioxide` |
| `Turbine 1 SO2 annual permit limit` | `sulfur_dioxide` |
| `Turbine 1 SO2 annual permitted limit` | `sulfur_dioxide` |
| `Turbine 1 SO2 emission rate` | `sulfur_dioxide` |
| `Turbine 1 SO2 permit limit` | `sulfur_dioxide` |
| `Turbine 1 SO2 permitted limit` | `sulfur_dioxide` |
| `Turbine 1, NOx, turbine-only mode, annualized` | `nitrogen_oxides` |
| `Turbine 1, NOx, turbine-only mode, three-test average` | `nitrogen_oxides` |
| `Turbine 1, SO₂, turbine-only mode, annualized; EXCEEDS permit limit 12.5 ton/yr` | `sulfur_dioxide` |
| `Turbine 1, SO₂, turbine-only mode, three-test average; EXCEEDS permit limit 2.9 lb/hr` | `sulfur_dioxide` |
| `Turbine 1, duct burner, SO₂, calculated, annualized; EXCEEDS permit limit 1.5 ton/yr` | `sulfur_dioxide` |
| `Turbine 1, duct burner, SO₂, calculated; EXCEEDS permit limit 0.3 lb/hr` | `sulfur_dioxide` |
| `Turbine 2 SO2 annual (8760 hrs/yr basis)` | `sulfur_dioxide` |
| `Turbine 2 SO2 annual emission rate` | `sulfur_dioxide` |
| `Turbine 2 SO2 annual permitted limit` | `sulfur_dioxide` |
| `Turbine 2 SO2 emission rate` | `sulfur_dioxide` |
| `Turbine 2 SO2 permitted limit` | `sulfur_dioxide` |
| `Turbine 2, NOx, turbine-only mode, annualized` | `nitrogen_oxides` |
| `Turbine 2, NOx, turbine-only mode, three-test average` | `nitrogen_oxides` |
| `Turbine 2, SO₂, turbine-only mode, annualized; EXCEEDS permit limit 12.5 ton/yr` | `sulfur_dioxide` |
| `Turbine 2, SO₂, turbine-only mode, three-test average; EXCEEDS permit limit 2.9 lb/hr` | `sulfur_dioxide` |
| `Turbine 2, duct burner, SO₂, calculated, annualized; EXCEEDS permit limit 1.5 ton/yr` | `sulfur_dioxide` |
| `Turbine 2, duct burner, SO₂, calculated; EXCEEDS permit limit 0.3 lb/hr` | `sulfur_dioxide` |
| `Turbine 3 SO2 annual (8760 hrs/yr basis)` | `sulfur_dioxide` |
| `Turbine 3 SO2 annual emission rate` | `sulfur_dioxide` |
| `Turbine 3 SO2 annual permitted limit` | `sulfur_dioxide` |
| `Turbine 3 SO2 emission rate` | `sulfur_dioxide` |
| `Turbine 3 SO2 permitted limit` | `sulfur_dioxide` |
| `Turbine 3, NOx, turbine-only mode, annualized; exceeds permit limit of 33.0 ton/yr if continuous` | `nitrogen_oxides` |
| `Turbine 3, NOx, turbine-only mode, three-test average` | `nitrogen_oxides` |
| `Turbine 3, SO₂, turbine-only mode, annualized; EXCEEDS permit limit 12.5 ton/yr` | `sulfur_dioxide` |
| `Turbine 3, SO₂, turbine-only mode, three-test average; EXCEEDS permit limit 2.9 lb/hr` | `sulfur_dioxide` |
| `Turbine 3, duct burner, SO₂, calculated, annualized; EXCEEDS permit limit 1.5 ton/yr` | `sulfur_dioxide` |
| `Turbine 3, duct burner, SO₂, calculated; EXCEEDS permit limit 0.3 lb/hr` | `sulfur_dioxide` |
| `Turbine 4 (EUTURBINE4-S3) NOx permit limit` | `nitrogen_oxides` |
| `Turbine 4 (EUTURBINE4-S3) SO₂ permit limit` | `sulfur_dioxide` |
| `Turbine 4 (Solar Taurus), NOx, three-test average` | `nitrogen_oxides` |
| `Turbine 4 NOx permit limit, annualized` | `nitrogen_oxides` |
| `Turbine 4 SO2 emission rate` | `sulfur_dioxide` |
| `Turbine 4 SO2 emissions` | `sulfur_dioxide` |
| `Turbine 4 SO2 permit limit` | `sulfur_dioxide` |
| `Turbine 4 SO2 permitted limit` | `sulfur_dioxide` |
| `Turbine 4, NOx, annualized` | `nitrogen_oxides` |
| `Turbine 4, SO₂; EXCEEDS permit limit 0.9 lb/MWhr` | `sulfur_dioxide` |
| `Turbine fuel sulfur content limit` | `other` |
| `Turbines 1-3 capacity` | `operational_capacity` |
| `Two enclosed flares combined capacity` | `operational_capacity` |
| `Typical minor leaks throughout process equipment inside building` | `methane_secondary` |
| `Typical slope of 25 percent or greater for cover slopes outside active operational area` | `other` |
| `Un-combusted landfill gas volume released, Feb 6–10, 2020` | `event_status` |
| `Unauthorized discharge through flume at east treatment pond` | `flow_wastewater` |
| `Unauthorized wastewater discharge from west treatment pond` | `flow_wastewater` |
| `Upper methane range consistent month-over-month` | `methane_secondary` |
| `Utility Flare landfill gas flowrate observed during inspection` | `operational_capacity` |
| `Utility flare inlet average volumetric flow rate` | `operational_capacity` |
| `VOC (NMHC), three-test average` | `nmoc_voc` |
| `VOC 12-month limit` | `nmoc_voc` |
| `VOC 12-month rolling limit for FGPROJECT23` | `nmoc_voc` |
| `VOC 12-month rolling limit for FGPROJECT23 (all four turbines combined)` | `nmoc_voc` |
| `VOC Post-run Mid gas` | `qa_sample` |
| `VOC Post-run Zero gas` | `qa_sample` |
| `VOC Pre-run Mid gas` | `qa_sample` |
| `VOC Pre-run Zero gas` | `qa_sample` |
| `VOC emission limit` | `nmoc_voc` |
| `VOC emission limit, PTI No. 179-13` | `nmoc_voc` |
| `VOC emission rate` | `nmoc_voc` |
| `VOC emission rate, McGill flare exhaust` | `nmoc_voc` |
| `VOC emissions` | `nmoc_voc` |
| `VOC emissions, 12-month rolling average for FGPROJECT23` | `nmoc_voc` |
| `VOC hourly limit for EUTURBINE4` | `nmoc_voc` |
| `VOC normal operation for FGTURBINES` | `nmoc_voc` |
| `VOC permit limit` | `nmoc_voc` |
| `VOC, EGT Turbine #1, Duct Burner OFF` | `nmoc_voc` |
| `VOC, EGT Turbine #1, Duct Burner OFF, Run average` | `nmoc_voc` |
| `VOC, EGT Turbine #1, Duct Burner ON` | `nmoc_voc` |
| `VOC, EGT Turbine #1, Duct Burner ON, Run average` | `nmoc_voc` |
| `VOC, EGT Turbine #3, Duct Burner OFF` | `nmoc_voc` |
| `VOC, EGT Turbine #3, Duct Burner OFF, Run average` | `nmoc_voc` |
| `VOC, EGT Turbine #3, Duct Burner ON` | `nmoc_voc` |
| `VOC, EGT Turbine #3, Duct Burner ON, Run average` | `nmoc_voc` |
| `VOC/NMOC actual emissions` | `nmoc_voc` |
| `VOC/NMOC actual emissions <1` | `nmoc_voc` |
| `VOC/NMOC potential emissions` | `nmoc_voc` |
| `Vacuum applied to landfill gas extraction system` | `pressure_vacuum` |
| `Vacuum in north end of landfill` | `pressure_vacuum` |
| `Vacuum on landfill before plant` | `pressure_vacuum` |
| `Vacuum reading during Run #2 at inlet to Gas Compressors` | `pressure_vacuum` |
| `Vacuum reading observed multiple times on flare control panel` | `pressure_vacuum` |
| `Vacuum setpoint for AHE plant` | `pressure_vacuum` |
| `Vacuum setpoint for flare blowers` | `pressure_vacuum` |
| `Vertical wells showing signs of liquid impairment (193 total liquid-impaired; 12 severe >100% submerged, 42 confirmed)` | `well_operational` |
| `Vinyl Chloride` | `btex_chlorinated_voc` |
| `Vinyl Chloride - Maximum Monthly Average` | `btex_chlorinated_voc` |
| `Vinyl Chloride Maximum Monthly Average` | `btex_chlorinated_voc` |
| `Vinyl Chloride Maximum Monthly Average (concentration)` | `btex_chlorinated_voc` |
| `Vinyl Chloride PEQ (potential effluent quality)` | `btex_chlorinated_voc` |
| `Vinyl Chloride concentration` | `btex_chlorinated_voc` |
| `Vinyl Chloride concentration maximum monthly average` | `btex_chlorinated_voc` |
| `Vinyl Chloride discharge limit` | `btex_chlorinated_voc` |
| `Vinyl Chloride in influent; PEL/FCV 13 ug/L` | `btex_chlorinated_voc` |
| `Vinyl Chloride maximum monthly average` | `btex_chlorinated_voc` |
| `Vinyl Chloride maximum monthly average concentration` | `btex_chlorinated_voc` |
| `Vinyl Chloride measurement` | `btex_chlorinated_voc` |
| `Vinyl Chloride monthly average limit` | `btex_chlorinated_voc` |
| `Vinyl Chloride recommended monthly limit` | `btex_chlorinated_voc` |
| `Vinyl Chloride, Maximum Monthly Average` | `btex_chlorinated_voc` |
| `Vinyl chloride - measured data point` | `btex_chlorinated_voc` |
| `Vinyl chloride - recommended monthly limit` | `btex_chlorinated_voc` |
| `Vinyl chloride PEL (FCV monthly avg)` | `btex_chlorinated_voc` |
| `Vinyl chloride PEQ (potential effluent quality)` | `btex_chlorinated_voc` |
| `Vinyl chloride in influent` | `btex_chlorinated_voc` |
| `Vinyl chloride maximum monthly average` | `btex_chlorinated_voc` |
| `Vinyl chloride monthly concentration limit` | `btex_chlorinated_voc` |
| `Vinyl chloride, ONYX-INF sample` | `btex_chlorinated_voc` |
| `Vinyl chloride, ONYX-INT sample` | `btex_chlorinated_voc` |
| `Visible emission limit for EU5000CFMFLARE` | `other` |
| `Visible emissions from flare exhaust in 2-hour observation period` | `event_status` |
| `Vitastim Nitrifiers discharge concentration approved` | `other` |
| `Vitastim Nitrifiers discharge concentration limit, Outfall 001` | `other` |
| `Volatile Organic Compounds (VOCs)` | `nmoc_voc` |
| `Volatile organic compounds (multiple analytes, all below 1.0 µg/L detection limit)` | `nmoc_voc` |
| `Wastewater discharge from west treatment pond` | `flow_wastewater` |
| `Wastewater removed from west pond for off-site disposal` | `flow_wastewater` |
| `Wastewater removed from west pond for off-site disposal in response to violation` | `flow_wastewater` |
| `Water Quality-Based Effluent Limit (WQBEL) for Pit Raider` | `other` |
| `Water level in Cell 4 after continued pumping` | `pressure_vacuum` |
| `Water level in Cell 4 riser pipe (inspector disputed this as measurement point anomaly)` | `pressure_vacuum` |
| `Water pumped from west pond by third-party contractor for off-site disposal` | `flow_wastewater` |
| `Water quality-based effluent limit for total mercury` | `mercury` |
| `Water quality-based effluent limit for total mercury (R 323.1103(9))` | `mercury` |
| `Water volume pumped from stormwater pond` | `flow_wastewater` |
| `Weather at 8:30–9:00 AM on inspection day` | `other` |
| `Weekly application dosage concentration for 3 weeks` | `other` |
| `Wellfield vacuum (negative, approximately -74 inches)` | `pressure_vacuum` |
| `Wellhead pressure exceedances Jan–Jun 2025, all corrected within 60 days` | `pressure_vacuum` |
| `Wellhead pressure standard exceeded during Jul 1 - Dec 31, 2024; all corrected within 15 days` | `pressure_vacuum` |
| `Wells fully saturated with liquid` | `well_operational` |
| `Wells fully saturated with liquid/leachate` | `well_operational` |
| `Wells fully submerged (Q2 2025)` | `well_operational` |
| `Wells online during inspection in Cell 4` | `well_operational` |
| `Wells with 75–95% screen submerged (Q2 2025)` | `well_operational` |
| `Wells with <10 inches water column available vacuum` | `pressure_vacuum` |
| `Wells with >50% of perforated screen submerged in liquid` | `well_operational` |
| `Wells with >50% perforated screen submerged in liquid` | `well_operational` |
| `Wells with >75% of perforated screen blocked by liquid` | `well_operational` |
| `Wells with >75% of screen blocked by leachate` | `well_operational` |
| `Wells with H₂S 500–999 ppm in Q1 2026` | `well_operational` |
| `Wells with H₂S >1000 ppm (Spring 2025)` | `well_operational` |
| `Wells with H₂S >1000 ppm in Q1 2026` | `well_operational` |
| `Wetland 1 area affected (PEM and PFO combined)—permanent impact` | `other` |
| `Wind gust speed (high end of range WNW)` | `wind_odor` |
| `Wind gust speed (low end of range WNW)` | `wind_odor` |
| `Wind gust speed evening complaint period` | `wind_odor` |
| `Wind speed NNW direction` | `wind_odor` |
| `Wind speed during October 20, 2022 investigation event` | `wind_odor` |
| `Wind speed from southwest, 6:00 AM - 10:30 AM window` | `wind_odor` |
| `Wind speed observed during inspection` | `wind_odor` |
| `Wind speed, direction WNW` | `wind_odor` |
| `Wind speed; direction WSW` | `wind_odor` |
| `Winter temperature` | `temperature_secondary` |
| `Xylene Total (below detection limit)` | `btex_chlorinated_voc` |
| `Zinc, Total` | `zinc` |
| `Zink blower test flow` | `operational_capacity` |
| `additional gas from top of hill wells opened in May` | `operational_capacity` |
| `ambient air temperature at time of inspection` | `temperature_secondary` |
| `ambient temperature at inspection time` | `temperature_secondary` |
| `average flow rate observed during bypass event` | `flow_wastewater` |
| `average landfill gas flow collected April 2026` | `operational_capacity` |
| `average leachate pumping rate` | `flow_wastewater` |
| `average odor complaints per month over last year` | `wind_odor` |
| `background methane level` | `methane` |
| `background methane on Chubb Road` | `methane` |
| `background methane upwind on Chubb Road` | `methane` |
| `background upwind methane on Chubb Road` | `methane` |
| `benzene` | `benzene` |
| `calculated landfill gas generation rate using LandGEM model for 2026` | `operational_capacity` |
| `calibration frequency` | `event_status` |
| `cis-1,2-Dichloroethylene, ONYX-INF sample` | `btex_chlorinated_voc` |
| `cis-1,2-Dichloroethylene, ONYX-INT sample` | `btex_chlorinated_voc` |
| `collected landfill gas released to atmosphere Feb 7–10, 2020` | `event_status` |
| `current gas flow` | `operational_capacity` |
| `downwind methane measured on Napier Road prior to SEM survey` | `methane` |
| `downwind methane on Napier Road with strong sewage-like smell` | `methane` |
| `duration from exceedance onset (1 PM) to clearance (3 PM)` | `event_status` |
| `estimated water volume discharged through overflow system based on 20 gpm average flow over ~23 hours` | `flow_wastewater` |
| `federal surface methane concentration standard (40 CFR 63.1958(d))` | `methane` |
| `flare capacity` | `operational_capacity` |
| `fuel sulfur content limit for turbines` | `other` |
| `gas plant shutdown duration (6am-9pm Monday)` | `event_status` |
| `humidity` | `other` |
| `hydrochloric acid (HCl) from enclosed flare FGENCLOSEDFLARES-S2` | `hydrogen_chloride` |
| `hydrogen sulfide` | `hydrogen_sulfide` |
| `hydrogen sulfide (H2S) 1-minute average` | `hydrogen_sulfide` |
| `hydrogen sulfide (H2S) 1-minute average, near Well 500R` | `hydrogen_sulfide` |
| `hydrogen sulfide, highest reading in May 2022` | `hydrogen_sulfide` |
| `hydrogen sulfide, leach sump with gas extraction` | `hydrogen_sulfide` |
| `landfill air compressor running at design capacity` | `pressure_vacuum` |
| `leachate pumping spike in May following broken horizontal leachate line; exceeds township limit` | `flow_wastewater` |
| `light LFG odor at 18823 Bayberry` | `wind_odor` |
| `light LFG odor at Napier Road gate at 7:25 pm` | `wind_odor` |
| `light LFG odor at Napier Road gate at 8:10 pm` | `wind_odor` |
| `light LFG odor on Briar Ridge to Northstar Way` | `wind_odor` |
| `locations exceeding 500 ppm methane threshold` | `exceedances_count` |
| `medium LFG odor on Napier from odor fans to end of waste collection fence` | `wind_odor` |
| `medium intensity LFG odor on 6 Mile from gate to Napier Road` | `wind_odor` |
| `mercury` | `mercury` |
| `mercury de minimis threshold per DEQ PPN 09-014` | `mercury` |
| `mercury from compost leachate` | `mercury` |
| `mercury influent` | `mercury` |
| `mercury, total` | `mercury` |
| `mercury, total — below reporting limit` | `mercury` |
| `methane above West haul road in small vents` | `methane` |
| `methane action level over 15-minute average per Consent Judgment 2020-0593-CE` | `methane` |
| `methane at 2 penetrations` | `methane` |
| `methane at AQD 1, East side of Cell 4E at Separation Berm` | `methane` |
| `methane at AQD 2, 200 feet NW of well manifold` | `methane` |
| `methane at AQD 3, 30 feet NW of well manifold` | `methane` |
| `methane at AQD 4, Just N of well manifold` | `methane` |
| `methane at AQD 5, Well 289 vacuum riser penetration` | `methane` |
| `methane at AQD 6, 30 feet East of Well 407` | `methane` |
| `methane at AQD 7, 10 feet NW of Well 440` | `methane` |
| `methane at AQD 8, 75 feet West of Well 422` | `methane` |
| `methane at Smoke Target, waist height, active working face NE side` | `methane` |
| `methane at access riser in line with Well 5-01` | `methane` |
| `methane at access riser north of Well 5-01` | `methane` |
| `methane at access riser on slope facing east to Cell 6` | `methane` |
| `methane at caisson well` | `methane` |
| `methane at caisson well Cell 4E` | `methane` |
| `methane at caisson well with damaged cap` | `methane` |
| `methane at caisson well with damaged cap, leaking at Fernco and cap` | `methane` |
| `methane at fence line downwind of gas well HW-23` | `methane` |
| `methane at liner tear location (MK-21)` | `methane` |
| `methane at penetration (MK-25)` | `methane` |
| `methane at penetration, highest reading in survey` | `methane` |
| `methane at waist height, active working face NE side` | `methane` |
| `methane at well` | `methane` |
| `methane at wellhead` | `methane` |
| `methane downwind on Napier Road prior to survey` | `methane` |
| `methane exceedance` | `methane` |
| `methane in landfill gas` | `methane_secondary` |
| `methane on 6-mile road prior to arrival at landfill` | `methane` |
| `methane perimeter reading along 6-Mile Road` | `methane` |
| `methane regulatory limit; measured exceedances noted at multiple surface locations` | `methane` |
| `methane surface concentration; 19 areas exceeded this level` | `methane` |
| `methane venting from spotter pipe (MK-16)` | `methane` |
| `methane; one high spot, no surface marking; >1 acre area with several hundred ppm` | `methane` |
| `minimum flare temperature requirement` | `other` |
| `minimum retention time in flare` | `other` |
| `monitoring interval frequency, not a measured reading` | `event_status` |
| `monitoring system downtime (12/7 5 PM to 12/8 1 PM)` | `event_status` |
| `non-methane organic compound (NMOC) emitted to atmosphere during flare malfunction` | `nmoc_voc` |
| `odor complaints received on single date` | `event_status` |
| `overall methane concentration in landfill gas, April 2026` | `methane_secondary` |
| `pH` | `ph` |
| `pH (00400) - Maximum Daily` | `ph` |
| `pH (00400) - Maximum Daily permit requirement` | `ph` |
| `pH (00400) - Minimum Daily` | `ph` |
| `pH (00400) - Minimum Daily permit requirement` | `ph` |
| `pH (maximum and minimum)` | `ph` |
| `pH - Maximum` | `ph` |
| `pH - Maximum Daily` | `ph` |
| `pH - Maximum Daily Limit` | `ph` |
| `pH - Maximum Daily limit` | `ph` |
| `pH - Minimum` | `ph` |
| `pH - Minimum Daily` | `ph` |
| `pH - Minimum Daily Limit` | `ph` |
| `pH - Minimum Daily limit` | `ph` |
| `pH - average` | `ph` |
| `pH - maximum` | `ph` |
| `pH Maximum Daily` | `ph` |
| `pH Maximum Daily limit` | `ph` |
| `pH Maximum Daily permit limit` | `ph` |
| `pH Maximum Daily permitted limit` | `ph` |
| `pH Minimum Daily` | `ph` |
| `pH Minimum Daily limit` | `ph` |
| `pH Minimum Daily permit limit` | `ph` |
| `pH Minimum Daily permitted limit` | `ph` |
| `pH at Final Effluent (Outfall 001A), exceeds permit limit of 9.0` | `ph` |
| `pH at Final Effluent, exceeds permit limit of 9.0` | `ph` |
| `pH average` | `ph` |
| `pH close to violation limit` | `ph` |
| `pH maximum` | `ph` |
| `pH maximum daily` | `ph` |
| `pH maximum daily limit` | `ph` |
| `pH maximum limit` | `ph` |
| `pH maximum weekly` | `ph` |
| `pH minimum` | `ph` |
| `pH minimum daily` | `ph` |
| `pH minimum daily limit` | `ph` |
| `pH minimum limit` | `ph` |
| `pH minimum weekly` | `ph` |
| `pH permit limit` | `ph` |
| `pH permit maximum daily` | `ph` |
| `pH permit minimum daily` | `ph` |
| `pH permit requirement maximum daily` | `ph` |
| `pH permit requirement minimum daily` | `ph` |
| `pH violation` | `ph` |
| `pH, Final Effluent (1)` | `ph` |
| `pH, Final Effluent (1); permit range 6.5–9.0 SU` | `ph` |
| `pH, Final Effluent, exceeds 9.0 S.U. maximum daily limit` | `ph` |
| `pH, Final Effluent, violation of 9.0 S.U. limit` | `ph` |
| `pH, Maximum Daily` | `ph` |
| `pH, Minimum Daily` | `ph` |
| `pH, maximum` | `ph` |
| `pH, maximum daily limit` | `ph` |
| `pH, minimum` | `ph` |
| `pH, minimum daily limit` | `ph` |
| `phenol — below reporting limit` | `other` |
| `regulatory threshold per 40 CFR 60.753(d)` | `methane` |
| `strong LFG odor next to weigh scales on 6 Mile Road` | `wind_odor` |
| `strong LFG odor on Napier from 6 Mile to just north of odor fans` | `wind_odor` |
| `sulfur dioxide (SO2) from enclosed flare FGENCLOSEDFLARES-S2` | `sulfur_dioxide` |
| `surface methane concentration regulatory threshold per 40 CFR 60.753(d)` | `methane` |
| `surface methane monitoring exceedances in 3rd quarter 2011` | `exceedances_count` |
| `surface methane monitoring exceedances in 4th quarter 2011` | `exceedances_count` |
| `surface methane threshold requiring quarterly monitoring and corrective action under NSPS Subpart WWW` | `surface_emissions` |
| `threshold for three separate methane plumes detected on perimeter` | `methane` |
| `total NOx emission rate limit` | `nitrogen_oxides` |
| `total backup flare capacity (McGill 4600 + Zink 3400 + Utility 5000)` | `operational_capacity` |
| `total vertical wells likely impaired by liquid` | `well_operational` |
| `total water pumped from west pond and hauled off-site by contractor` | `flow_wastewater` |
| `township leachate discharge limit` | `flow_wastewater` |
| `trans-1,2-Dichloroethene, influent` | `btex_chlorinated_voc` |
| `trans-1,2-Dichloroethene, intermediate stage` | `btex_chlorinated_voc` |
| `trans-1,2-dichloroethene` | `btex_chlorinated_voc` |
| `trans-1,2-dichloroethene discharge limit` | `btex_chlorinated_voc` |
| `treated groundwater discharge authorization` | `flow_wastewater` |
| `uncontrolled gas release from groundhog holes near TS-01` | `operational_capacity` |
| `vacuum applied to wellfield` | `pressure_vacuum` |
| `vacuum applied to wells in cell 4E` | `pressure_vacuum` |
| `vacuum from Fortistar gas-to-electric plant (2-month average)` | `pressure_vacuum` |
| `vacuum loss East side header under railroad crossing` | `pressure_vacuum` |
| `vacuum loss West side header` | `pressure_vacuum` |
| `vertical gas wells needing complete replacement, Q1 2020` | `well_operational` |
| `vertical gas wells with >50% screen blocked by liquid, Q1 2020` | `well_operational` |
| `vertical wells showing signs of impairment` | `well_operational` |
| `vertical wells with 100% screen submerged, Q1 2020` | `well_operational` |
| `visible emissions opacity limit (6-minute average)` | `particulate_matter` |
| `wells showing signs of air infiltration` | `well_operational` |
| `wells with H2S concentrations above 1000 ppm in Q1 2026` | `well_operational` |
| `wells with H2S concentrations between 500-999 ppm in Q1 2026` | `well_operational` |
| `wells with better than −20 in wc vacuum (75% of 250 total)` | `well_operational` |
| `wells with confirmed liquid impairment` | `well_operational` |
| `wells with high % methane/vapor locked` | `well_operational` |
| `wells with low applied well vacuum` | `well_operational` |
| `wells with severe liquid impairment (screen >100% submerged)` | `well_operational` |
| `wells with worse than −15 in wc vacuum (15% of 250 total)` | `well_operational` |
| `wells with −15 to −20 in wc vacuum (10% of 250 total)` | `well_operational` |
| `wind speed NW direction` | `wind_odor` |
| `wind speed WNW direction, less than 10 mph` | `wind_odor` |
| `wind speed from NE at time of exceedances` | `wind_odor` |
| `wind speed from west` | `wind_odor` |
| `wind speed, ENE direction` | `wind_odor` |
| `wind speed, direction WSW` | `wind_odor` |
