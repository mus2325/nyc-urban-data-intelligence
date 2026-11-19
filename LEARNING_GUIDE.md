# Learning guide

## Run the project on macOS

1. Install Python 3.12 from [Python.org](https://www.python.org/downloads/macos/) and open Terminal. Run `python3.12 --version` to confirm that Terminal can find it.
2. Clone the repository and enter it. If you already have a checkout, start in that directory. GitHub can also provide a downloadable ZIP.

   ```bash
   git clone https://github.com/mus2325/nyc-urban-data-intelligence.git
   cd nyc-urban-data-intelligence
   ```

   To try the branch during review, run `git fetch origin` and `git switch --track origin/feat/nyc-311-mvp` once the branch is on GitHub.

3. Create and activate an isolated environment, then install the libraries.

   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

   The virtual environment gives this project its own packages. Activate it when you open a new Terminal window. Use `deactivate` when you finish.

4. Start small. Read the log messages for fetched, rejected, duplicate, and inserted records.

   ```bash
   python -m nyc311.etl --limit 50
   python -m streamlit run app.py --browser.gatherUsageStats false
   ```

   Open [localhost:8501](http://localhost:8501) and pick a borough. Check how the three charts and request count respond. Press **Control+C** to stop Streamlit.

5. Run the automated tests from the repository root.

   ```bash
   python -m unittest discover -v
   ```

## Follow a request through the pipeline

1. **Extract.** An API is an interface programs can call over the network. Requests sends an HTTPS request to NYC Open Data. The query selects four fields and caps the result size so a first run is small. The response is JSON, which Python reads as a list of dictionaries.
2. **Transform.** Pandas turns the dictionaries into a DataFrame, a table with named columns. The code trims text, parses local NYC timestamps, and rejects rows with unusable IDs, dates, or categories. It labels missing or unrecognized boroughs as `UNKNOWN` so those records still count.
3. **Load.** SQLite stores the table in a file. The primary key makes each request ID unique even across multiple ETL runs. A transaction commits a batch together or rolls back the inserts if a write fails. Replaying the same records is idempotent because those IDs already exist.
4. **Explore.** Streamlit reads the database and reruns the dashboard code when you change a filter. Pandas counts categories and boroughs and groups requests by day. The dashboard uses a read-only connection, so filtering has no effect on stored data.

Logs explain what each ETL run did. Errors produce a nonzero exit code that a shell or future scheduler can detect. Git ignores the virtual environment and generated database because those are local artifacts.

## Inspect the database

macOS typically provides the `sqlite3` command. After the ETL succeeds, run this from the repository root.

```bash
sqlite3 -readonly data/nyc311.sqlite3 'SELECT borough, COUNT(*) FROM requests GROUP BY borough ORDER BY COUNT(*) DESC;'
```

Run the ETL again, then repeat the query. Recent requests may have arrived in the meantime. Existing IDs will be skipped.

If the dashboard says the database is missing, run the ETL first and check its output. If you see a proxy or connection error, check that your network allows access to `data.cityofnewyork.us`. You can still run the offline tests with installed dependencies.

The [README architecture diagram and AWS mapping](README.md#architecture) show where the same responsibilities could live in a cloud design. This project needs only your computer and public NYC Open Data access.
