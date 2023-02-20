from datetime import datetime, date
import dateutil
from typing import List, Dict, Any
from bs4 import BeautifulSoup
import requests
import re
import json
import pandas as pd
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
from google.oauth2.service_account import Credentials
from airflow import DAG
from airflow.decorators import dag, task
from airflow.operators.dummy import DummyOperator

# dict of cities with URLS to loop through and scrape
CITIES = [
    {
        "Name": "Portland, OR",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=45.5118&lon=-122.6756",
    },
    {
        "Name": "San Diego, CA",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=32.7157&lon=-117.1617",
    },
    {
        "Name": "Duluth, MN",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=46.788&lon=-92.0998",
    },
    {
        "Name": "Minneapolis, MN",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=44.979&lon=-93.2649",
    },
    {
        "Name": "Salt Lake City, UT",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?zoneid=UTZ105&zflg=1",
    },
    {
        "Name": "Denver, CO",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=39.74&lon=-104.992",
    },
    {
        "Name": "San Francisco, CA",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=37.7771&lon=-122.4197",
    },
    {
        "Name": "New York City, NY",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=40.7143&lon=-74.006",
    },
    {
        "Name": "Portland, ME",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=43.6592&lon=-70.2567",
    },
    {
        "Name": "Seattle, WA",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=47.6036&lon=-122.3294",
    },
    {
        "Name": "Baltimore, MD",
        "NWS_URL": "https://forecast.weather.gov/MapClick.php?lat=39.2906&lon=-76.6093",
    },
]


@task
def scrape_weather_data():
    """scrapes weather from national weather service and turns to json

    Returns:
        json of weather data
    """

    data = []
    # loop through cities using beautiful soup
    for city in CITIES:
        try:
            response = requests.get(city["NWS_URL"])
        except:
            continue
        soup = BeautifulSoup(response.content, "html.parser")

        # scrape location
        location = soup.find("h2", {"class": "panel-title"})

        # scrape lat, lon and elv
        lat_lon_elev = soup.find("span", {"class": "smallTxt"}).text.strip()
        lat, lon, elev = re.findall(r"[-+]?\d*\.\d+|\d+", lat_lon_elev)

        temperature = soup.find("p", {"class": "myforecast-current-lrg"})

        humidity_elem = soup.find("td", text="Humidity")
        humidity = humidity_elem.find_next("td").text.strip() if humidity_elem else None

        wind_speed_elem = soup.find("td", text="Wind Speed")
        wind_speed = (
            wind_speed_elem.find_next("td").text.strip() if wind_speed_elem else None
        )

        barometer_elem = soup.find("td", text="Barometer")
        barometer = (
            barometer_elem.find_next("td").text.strip() if barometer_elem else None
        )

        dewpoint_elem = soup.find("td", text="Dewpoint")
        dewpoint = dewpoint_elem.find_next("td").text.strip() if dewpoint_elem else None

        visibility_elem = soup.find("td", text="Visibility")
        visibility = (
            visibility_elem.find_next("td").text.strip() if visibility_elem else None
        )

        wind_chill_elem = soup.find("td", text="Wind Chill")
        wind_chill = (
            wind_chill_elem.find_next("td").text.strip() if wind_chill_elem else None
        )

        last_update_elem = soup.find("td", text="Last update")
        last_update = (
            last_update_elem.find_next("td").text.strip() if last_update_elem else None
        )

        # appends scraped data in following format:
        data.append(
            {
                "location": city["Name"],
                "lat": lat,
                "lon": lon,
                "elev_ft": elev,
                "temperature": temperature.text if temperature else None,
                "humidity": humidity,
                "wind_speed": wind_speed,
                "barometer": barometer,
                "dewpoint": dewpoint,
                "vis_miles": visibility,
                "wind_chill": wind_chill,
                "last_update": last_update,
            }
        )

    df = pd.DataFrame(data)
    data = df.to_json(orient="records")

    return data


@task
def transform_weather_data(data):
    """read json into df and transforms data

    Args:
        data (json from scrape func)

    Returns:
        json of transformed data
    """

    # read json
    df = pd.read_json(data, orient="records")
    # separate city and state from location and set as string
    df[["city", "state"]] = df["location"].str.split(", ", expand=True)
    df["city"] = df["city"].astype(str)
    df["state"] = df["state"].astype(str)

    # convert lat and lon to float
    df[["lat", "lon"]] = df[["lat", "lon"]].astype(float)

    # convert elev_ft to int
    df["elev_ft"] = df.apply(
        lambda row: int(row["elev_ft"]) if row["elev_ft"] != "NA" else None, axis=1
    )

    # make conversions and separate columns for temp_f and temp_c convert to int
    df["temp_f"] = df["temperature"].str.extract("(\d+)").astype(int)
    df["temp_c"] = (df["temp_f"] - 32) * 5 / 9
    df["temp_c"] = df["temp_c"].round().astype(int)

    # convert humidity percentage to float
    df["humidity"] = (
        df["humidity"].str.extract("(\d+)", expand=False).astype(float) / 100
    )

    # convert wind_speed to int
    df["wind_speed"] = (
        df["wind_speed"].str.extract("(\d+)", expand=False).fillna(0).astype(int)
    )
    df["wind_speed"] = df["wind_speed"].replace("Calm", 0)

    # convert barometer to millibars and display as float
    df["barometer"] = df["barometer"].apply(
        lambda x: float(x.split()[0]) * 33.8639 if "in" in x and x != "NA" else None
    )
    df["barometer"] = df["barometer"].round(2)

    # create two columns for dewpoint_f and dewpoint_c and convert to int
    df[["dewpoint_f", "dewpoint_c"]] = (
        df["dewpoint"].str.extract("(\d+).*?(\d+)", expand=True).astype(int)
    )

    # strip and convert vis_miles to float
    df["vis_miles"] = (
        df["vis_miles"]
        .str.extract("(\d+\.\d+|\d+)", expand=False)
        .astype(float)
        .round(2)
    )

    # create two columns for windchill_f and windchill_c and convert to float
    df[["wind_chill_f", "wind_chill_c"]] = (
        df["wind_chill"]
        .str.extract("(\d+).*?(\d+)", expand=True)
        .astype(float)
        .fillna(0)
    )
    df[["wind_chill_f", "wind_chill_c"]] = (
        df["wind_chill"]
        .str.extract("(\d+).*?(\d+)", expand=True)
        .astype(float)
        .fillna(0)
        .replace(0, None)
    )

    # convert last update to timestamp
    df["last_update"] = df["last_update"].apply(
        lambda x: dateutil.parser.parse(
            x, tzinfos={"CST": dateutil.tz.tzoffset(None, -21600)}
        ).astimezone(dateutil.tz.tzutc())
    )
    df["last_update"] = df["last_update"].apply(
        lambda x: x.strftime("%Y-%m-%d %H:%M:%S.%f")
    )

    # drop unneeded columns
    df = df.drop(["temperature", "dewpoint", "wind_chill"], axis=1)

    # put None for Null
    df = df.where(pd.notnull(df), None)

    # Convert dataframe to json
    data = df.to_json(orient="records")

    return data


@task
def write_weather_data_to_bigquery(data):
    """reads json into df and loads df into bigquery

    Args:
        data (json from transform func)
    """

    PROJECT_ID = "team-week3"
    DATASET_ID = "weather-dw"
    DAILY_TABLE_ID = "daily"

    SCHEMA = [
        {"name": "location", "type": "STRING", "mode": "REQUIRED"},
        {"name": "lat", "type": "FLOAT", "mode": "REQUIRED"},
        {"name": "lon", "type": "FLOAT", "mode": "REQUIRED"},
        {"name": "elev_ft", "type": "INTEGER", "mode": "REQUIRED"},
        {"name": "humidity", "type": "FLOAT", "mode": "REQUIRED"},
        {"name": "wind_speed", "type": "INTEGER", "mode": "REQUIRED"},
        {"name": "barometer", "type": "FLOAT", "mode": "REQUIRED"},
        {"name": "vis_miles", "type": "FLOAT", "mode": "REQUIRED"},
        {"name": "dewpoint_f", "type": "INTEGER", "mode": "REQUIRED"},
        {"name": "dewpoint_c", "type": "INTEGER", "mode": "REQUIRED"},
        {"name": "wind_chill_f", "type": "FLOAT", "mode": "REQUIRED"},
        {"name": "wind_chill_c", "type": "FLOAT", "mode": "REQUIRED"},
        {"name": "city", "type": "STRING", "mode": "REQUIRED"},
        {"name": "state", "type": "STRING", "mode": "REQUIRED"},
        {"name": "temp_f", "type": "INTEGER", "mode": "REQUIRED"},
        {"name": "temp_c", "type": "INTEGER", "mode": "REQUIRED"},
        {"name": "last_update", "type": "STRING", "mode": "REQUIRED"},
    ]

    df = pd.read_json(data, orient="records")

    client = bigquery.Client()

    try:
        dataset_ref = client.dataset(DATASET_ID)
        dataset = client.get_dataset(dataset_ref)
    except NotFound:
        dataset_ref = client.dataset(DATASET_ID)
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = "US"
        dataset = client.create_dataset(dataset)

    table_ref = dataset.table(DAILY_TABLE_ID)

    try:
        client.get_table(table_ref)
    except NotFound:
        table = bigquery.Table(table_ref, schema=SCHEMA)
        table = client.create_table(table)

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND
    )
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()


@dag(
    "weather_data_pipeline",
    description="Scrapes National Weather Service website every 12 hours, transforms data and loads to bigquery",
    start_date=datetime.utcnow(),
    schedule_interval="0 0,12 * * *",
)
def weather_data_pipeline():
    """define tasks and task dependencies"""

    scrape_weather_data_task = scrape_weather_data()

    transform_weather_data_task = transform_weather_data(scrape_weather_data_task)

    write_weather_data_to_bigquery_task = write_weather_data_to_bigquery(
        transform_weather_data_task
    )

    done = DummyOperator(task_id="done")

    (
        scrape_weather_data_task
        >> transform_weather_data_task
        >> write_weather_data_to_bigquery_task
        >> done
    )


dag = weather_data_pipeline()
