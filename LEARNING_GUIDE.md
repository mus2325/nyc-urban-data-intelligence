# Learning guide

## Run the project on macOS

1. Install Python 3.12 from [Python.org](https://www.python.org/downloads/macos/) and open Terminal. Run `python3.12 --version` to confirm that Terminal can find it.
2. Clone the repository and enter it. If you already have a checkout, start in that directory. GitHub can also provide a downloadable ZIP.

   ```bash
   git clone https://github.com/mus2325/nyc-urban-data-intelligence.git
   cd nyc-urban-data-intelligence
   ```

   To try the branch during review, run `git fetch origin` and `git switch --track origin/feat/incremental-ingestion` once the branch is on GitHub.

3. Create and activate an isolated environment, then install the libraries.

   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

   The virtual environment gives this project its own packages. Activate it when you open a new Terminal window. Use `deactivate` when you finish.

4. Start small. Read the log messages for fetched, accepted, rejected, new, updated, and unchanged records.

   ```bash
   python -m nyc311.etl --page-size 50
   python -m streamlit run app.py --browser.gatherUsageStats false
   ```

   Open [localhost:8501](http://localhost:8501) and pick a borough. Check how the three charts and request count respond. Press **Control+C** to stop Streamlit.

5. Run the automated tests from the repository root.

   ```bash
   python -m unittest discover -v
   ```

## Follow a request through the pipeline

1. **Extract.** An API is an interface programs can call over the network. Requests sends an HTTPS request to NYC Open Data. Each query asks for the dashboard fields and the system modification timestamp. The page size caps how many records each request returns. The response is JSON, which Python reads as a list of dictionaries.
2. **Transform.** Pandas turns the dictionaries into a DataFrame, a table with named columns. The code trims text, parses local NYC timestamps, and rejects rows with unusable IDs, dates, or categories. It labels missing or unrecognized boroughs as `UNKNOWN` so those records still count.
3. **Load.** SQLite stores the table in a file. The primary key makes each request ID unique even across multiple ETL runs. An upsert inserts a new ID or changes the stored version of an existing ID. A transaction saves the rows and checkpoint together. Replaying the same source version leaves the dashboard records unchanged, which is an example of idempotence.
4. **Explore.** Streamlit reads the database and reruns the dashboard code when you change a filter. Pandas counts categories and boroughs and groups requests by day. The dashboard uses a read-only connection, so filtering has no effect on stored data.

Logs explain what each ETL run did. Errors produce a nonzero exit code that a shell or future scheduler can detect. Git ignores the virtual environment and generated database because those are local artifacts.

## How the next run knows where to start

Think of the watermark as the end time of the last fully scanned window. SQLite stores it as a checkpoint. Each run re-reads the 10 minutes before that time so a recently delayed row can still be collected. The scan uses the source's modification time to catch edits to older requests. The dashboard still uses creation time for its trends.

Pagination splits large responses into pages. If the previous page ended at time 10:05 with ID 250, the next page can include IDs greater than 250 at 10:05, as well as requests modified later. Saving the checkpoint only after all pages are read and written lets a failed run retry the same range without leaving partial updates in SQLite.

The first run has no checkpoint, so it starts one hour before the command runs. To retry a first run from the same starting point, choose a time with `--since` and reuse it until that run succeeds. For example, `--since 2026-11-19T21:00:00Z` selects 21:00 UTC. Omit this flag once there is a checkpoint. Each later run advances by at most a day, so catching up after a long break can take several runs.

## Inspect the database

macOS typically provides the `sqlite3` command. After the ETL succeeds, run this from the repository root.

```bash
sqlite3 -readonly data/nyc311.sqlite3 'SELECT borough, COUNT(*) FROM requests GROUP BY borough ORDER BY COUNT(*) DESC;'
```

Run the ETL again, then repeat the query. Recent requests may have arrived or changed in the meantime. Compare the new, updated, and unchanged counts in the ETL log.

If the dashboard says the database is missing, run the ETL first and check its output. If you see a proxy or connection error, check that your network allows access to `data.cityofnewyork.us`. You can still run the offline tests with installed dependencies.

To inspect the checkpoint and the last successful run's quality counts, use:

```bash
sqlite3 -readonly data/nyc311.sqlite3 'SELECT watermark, last_successful_run, quality_counts FROM ingestion_state;'
```

## Relate the pipeline to AWS Cloud Practitioner concepts

Run and study this project locally with public NYC Open Data access. These examples explain how common AWS services divide the same responsibilities.

- **Lambda** provides serverless compute. An ETL handler could run for each scheduled ingestion.
- **EventBridge Scheduler** can invoke that handler on a recurring schedule.
- **S3** is object storage. A future version could archive the original API pages so a failed transformation can be replayed.
- **RDS** provides managed relational databases. An RDS database could hold the records and checkpoint together when the dashboard and ETL run on different machines.
- **IAM** controls access. An IAM role could grant the ETL only the permissions needed for its archive and database.
- **CloudWatch** collects logs and metrics. A cloud ETL could log rejected counts and use an alarm to flag a missed successful run.

## See your tests run on GitHub

Continuous integration (CI) runs automated checks as you develop. The [CI workflow](.github/workflows/ci.yml) runs when a pull request targeting `main` is opened, reopened, or updated with new commits. GitHub starts a fresh Ubuntu runner, checks out the proposed code, sets up Python 3.12, installs `requirements.txt`, and runs the same `python -m unittest discover -v` command you use locally.

The tests simulate NYC Open Data responses and use temporary SQLite files. You can run CI without configuring an API key, repository secret, AWS account, or paid service.

On GitHub, open the pull request and select **Checks** to see the **Python tests** job. Expand **Run tests** to see each result. If the job fails, open the failed step to see the error, fix it locally, and push another commit to the pull request branch. You can also find the run under the repository's **Actions** tab by selecting **CI**.
