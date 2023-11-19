from bs4 import BeautifulSoup
from httpx import AsyncClient
import re
import json
import pandas as pd

async def get_soup(url: str, client: AsyncClient) -> BeautifulSoup:
    resp = await client.get(url)
    resp.raise_for_status()
    html = resp.text
    return BeautifulSoup(html, 'html.parser')

