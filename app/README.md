# FOB Power Planner (MA481X Project 2, Tactical Power Challenge)

Start: double-click `Start FOB Power Planner.bat` one folder up (or `python server.py` here). It opens http://127.0.0.1:8481.
Needs Python 3 with numpy and scipy (scipy ships the HiGHS MILP solver). No other installs.

Files
- `model.py`: model (U) of Scioletti et al. (2017) as a MILP on HiGHS, plus the added tiers, grid, owned equipment
  and representative days; heuristic screening; nonlinear re-simulation; approximation-error metrics (paper eqs. 26-29).
- `server.py`: local server; NASA POWER climate fetch (only coordinates leave the laptop), cached in `data/`.
- `index.html`: the app (isometric FOB builder, tiers, energy, dispatch, PACE, resupply, what-ifs, model and error, spec sheet).
- `data/`: 8 preset sites with real 2023 hourly NASA data (work offline).
- `../References/`: the Scioletti 2017 paper (OSTI copy) and its extracted text.

Still open for the team
- Building load numbers are JARVIS planning estimates (marked "estimate" in the app); replace them with sourced figures.
- 200 kW generator and 500/1000 kWh batteries are scaled estimates; Project task 1 asks for real equipment specs.
- Resupply method data other than the AEPI convoy casualty factor are estimates; verify the AEPI figure too.

## Update 16 Nov 2026 (round 2): deployment playback
- Player: speed slider (6 game-minutes/s up to ~10 months/s), back/forward one day, scrub bar across the whole operating period, start season. The deployment is stitched from the sample days (season changes every 91.3 days).
- Fuel ledger on top of the hourly dispatch: storage drains with the generators' burn, a delivery is ordered at the reorder point, the method is picked to match the delivery-mix shares, and it arrives after that method's lead time (new "lead time (h)" column, estimates). A failed reliability draw makes it 1-4 days late. The What-if resupply cut holds deliveries from its start day until it ends. If fuel runs out, generators stop and battery + solar carry Tier 1 only.
- Animations: convoy trucks, CH-47 sling loads, C-130 airdrop with parachutes, contractor tankers from port; strikes (indirect fire, UAS with counter-UAS intercepts) knock items out until repaired (repair times are estimates). Lost capacity sheds T3, then T2, then T1 (a playback approximation, not re-optimized).
- HUD: fuel gauge with reorder mark, next delivery ETA, risk so far (deliveries, late, exposure, expected casualties, strikes). Event log.
- Sound synthesized in the browser (generator hum, rotors, trucks, C-130, impacts); mute button.
- COAs tab: save layouts, compare side by side (best/worst highlighted), load back. Stored in the browser's localStorage.
- Luck is seeded (reroll button), so every COA sees the same delays and strikes.

## Update 16 Nov 2026 (round 3)
- Auto PACE switching (PACE tab): steps down to A / C / E below set fuel levels (defaults 40 / 20 / 8%), powering only that level's tiers; generator burn scales with load served (floor 30%, playback approximation). Fuel out drops to the battery + solar level. Badge in the HUD; fuel-over-deployment chart on the Resupply tab.
- Brief mode (header): 10 guided steps (site, build, tiers, optimize, model error, PACE, 3-month run with a 14-day cut from D+20 and 2 strikes/month, results, COA compare, deliverables). Exit restores the cut/threat settings. Saves two COAs named "Brief: ..." (re-running replaces them).
- Map: power cables from the power yard (tier colour, dark when cut, flowing while playing), fuel bladders that shrink/grow, soldiers walking by time of day (few at night), sun/moon arc, cloud and dust over the solar field.
- Excel button: Summary, Hourly (sample days), Deployment by day, Events, COAs, Inputs and sources (vendored SheetJS 0.18.5, vendor/xlsx.full.min.js). Saves to Downloads.
- Save file / Open file: whole scenario (and saved COAs) as JSON; opened files are checked field by field.
- Spec sheet "2-page" tick adds an appendix: fuel over the deployment, COA table, approximation-error table + chart, playback highlights.

## Update 16 Nov 2026 (round 4): realism and research
- Wires removed. No pop-up toasts during playback (alarms flash the fuel box instead); layout notes fold into one chip (click to open).
- Realistic drawing: TEMPER-style tents with ECUs, containerized shower/latrine/laundry, guard towers on legs, SATCOM dish, rotating C-UAS radar, generators with exhaust, tilted solar racks, battery container, HLZ pad, HESCO with mesh, concertina wire, T-wall gate, gravel roads. Existing FOB mode draws CHU rows and hard buildings. Terrain, walls and roads are cached, so a frame takes about 3-4 ms.
- Terrain by region (Site tab, "Terrain look"): Auto picks desert, jungle, mountain/high desert, temperate or arctic from latitude, annual sunshine and temperature. Checked on all 8 presets: Iraq, Kuwait, Djibouti = desert; Bagram = mountain; Luzon = jungle; Poland = temperate; Alaska, Norway = arctic.
- Building list from online research (sources shown in each building's inspector): Force Provider modules have containerized latrines, showers and batch laundry and TEMPER tents (army.mil 2010/2017, Wikipedia "Force Provider"); outposts in Afghanistan listed HLZ, DFAC, fuel point, motor pool, medical, generators, HESCO and towers (Health.mil POEMS, USACE guide). New: latrine, laundry, HLZ; motor pool renamed. MWR/gym is an add-on kit (army.mil 2017), so the template adds it only for a BN FOB or a deployment over 6 months.

## Update 16 Nov 2026 (round 5): sourced loads, region kits, build standards, strip
- Building counts now follow PNNL-23133 (2014, SAGE) App. B. 150-Soldier camp: 8 billet tents, 1 dining tent, 2 latrine, 1 laundry, 2 shower Tricons. 600-Soldier camp: 32 billets, 4 dining, 8 latrine, 4 laundry, 8 shower, plus MWR game room, gym and theater tents. The CO template now matches the 150-Soldier list exactly.
- Tent ECU: 8.8 kW base x 1.2 hottest-hour factor = 10.6 kW, the 60k BTU/h IECU maximum draw (PM MEP prototype data, DTIC briefing). This raised the CO total cost to about $5.65M in Iraq. Showers: the water heaters are fuel-fired (AWH 400), so only pumps and lights are electric. TOC: field battalion CP averaged about 2 kW on generators over 15 kW (army.mil "Watts next"); the FOB TOC values are estimates.
- Tiers tab "Check against published data": the PNNL 150-Soldier camp used 346 gal/day on spot generation and 242 gal/day with microgrid + battery + 52 kW PV (Table 3-3, 24 h TIAX profile). Our Iraq CO optimized: 269 gal/day.
- Build standard from the operating period: initial 0-6 months (no laundry or MWR, field kitchen at 35% electric), temporary 6-24 (Force Provider; MWR for BN), semi-permanent 24+ (CHUs and hard buildings at half HVAC, MWR for all). The months slider now goes to 36. Changing the standard or region rebuilds the buildings and keeps the power yard.
- Region kits: jungle = extra ROWPU, drainage pumps, ECUs never below 35% (dehumidify); arctic = vehicle warming tent; desert = HVAC x1.1 (dust). These are estimates; the Force Provider cold-weather kit exists (army.mil 2017).
- Playback info moved to one strip under the map; the event log is now a "Log" tab. Shorter legend.

## Update 16 Nov 2026 (round 6): over-time charts
- New "Over time" tab. Cost over the deployment: running total or per week, stacked buy cost (day 0) + fuel at the fully burdened price as it burns + grid; the lost-power penalty (model price, not money) is hidden by default. Energy over the deployment: per day or per week, MWh by source (generators, solar, battery, grid) plus unserved, with fuel burned (gal) on the right axis. Both have a gold playhead that follows the playback clock.
- Both come from the playback, so PACE step-downs, fuel-outs, strikes and the resupply cut show up. The playback total can differ from the KPI total (e.g., Iraq CO with a 20-day cut: 90,423 gal in playback vs 98,074 gal in the simulation without PACE shedding).
