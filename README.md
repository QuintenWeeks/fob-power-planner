# FOB Power Planner (MA481X Project 2, Tactical Power Challenge)

## Open it with nothing installed (recommended)

1. Sign in to GitHub and open this repository.
2. Click the green **Code** button, open the **Codespaces** tab, and click **Create codespace on main**.
3. Wait 2 to 3 minutes the first time while it sets up Python, numpy and scipy. If a box asks
   **"Do you trust the authors of the files in this folder?"**, click **Trust Folder & Continue** (the server won't
   start otherwise). A terminal at the bottom then shows `FOB Power Planner on http://127.0.0.1:8481`.
   Leave that terminal running.
4. The app opens in a new browser tab. If it doesn't (pop-up blocker), open the **Ports** tab at the bottom
   and click the globe icon next to port **8481**.

Next time, click **Code > Codespaces** and pick your existing codespace; it opens much faster.
When you're done, close the tab. The codespace stops on its own after 30 minutes idle.
Free accounts get about 60 hours a month on the 2-core machine.

If the app tab says the server isn't running, click the terminal and run:

```
python app/server.py --no-browser
```

## Run it on your own laptop instead

1. Install Python 3 (python.org, tick **Add python.exe to PATH**, or "Python 3.12" from the Microsoft Store).
2. In a new Command Prompt: `python -m pip install numpy scipy`
3. Download this repo (**Code > Download ZIP**), unzip it, and double-click `Start FOB Power Planner.bat`.
   Or run `python app/server.py` from the unzipped folder.
4. Go to http://127.0.0.1:8481 and leave the black window open.

## Sharing work
- **Save file / Open file** in the app passes a whole scenario (and saved COAs) as a JSON file.
  Saved COAs otherwise live only in your own browser.
- **Excel** exports a workbook. In a codespace it downloads through your browser.

## What's in here
- `app/`: the app (`index.html`), the model (`model.py`, Scioletti et al. 2017 model U on the HiGHS MILP solver),
  the local server (`server.py`), 8 preset sites of 2023 NASA POWER data, and the chart/map/Excel libraries.
  Details and change history: `app/README.md`.
- `References/`: the Scioletti 2017 paper (OSTI copy) and its text.
- `docs/`: client scope questions.

Still open for the team: the building loads, the 200 kW generator, the 500/1000 kWh batteries and most resupply
data are estimates and are marked "estimate" in the app.
