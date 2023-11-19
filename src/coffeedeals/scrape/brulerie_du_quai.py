from bs4 import BeautifulSoup
from httpx import AsyncClient
import re
import json
import pandas as pd
from coffeedeals.utils import get_soup

roast_map = {
    'torr:Moyen': 'Medium',
    'torr:Léger': 'Light',
    'torr:Léger Plus': 'Light/Medium'
}

def translate_roast(tags: list[str]) -> str:
    try:
        torr = next(filter(lambda tag: tag[0:4] == 'torr', tags))
        return roast_map.get(torr, torr)
    except StopIteration:
        return 'unknown'
    
def to_grams(weight: str) -> int:
    m = re.match('^(\d+)\s?(k?g)', weight)
    if not m:
        return None
    val = int(m[1])
    if m[2] == 'g':
        return val
    else:
        return 1000 * val


def to_dataframe(data: dict) -> pd.DataFrame:
    df = pd.DataFrame(data['variants'])
    df.drop(df.query('not available').index, inplace=True)
    df['grams'] = df.option1.apply(to_grams)
    df.dropna(subset='grams', inplace=True)
    df['price_per_gram'] = df['price'] / df['grams']
    df['name'] = data['title']
    df['description'] = BeautifulSoup(data['description'], 'html.parser').text
    df['roast'] = translate_roast(data['tags'])
    return df[['name', 'roast', 'grams', 'price', 'price_per_gram', 'description']]

async def scrape() -> pd.DataFrame:
    async with AsyncClient(base_url='https://www.brulerieduquai.com/en/collections/cafe') as client:
        dfs = []
        main_page = await get_soup('/', client)
        coffees = [a['href'].split('/')[-1] for a in main_page.find_all('a') 
                if a['href'].startswith('/en/collections/cafe/products/')][::2]
        for coffee in coffees:
            coffeesoup = await get_soup(f'/products/{coffee}', client)
            s = coffeesoup.find_all('script.bold-subscriptions-platform-script')
            s = [s for s in coffeesoup.find_all('script') if 'sswApp.product =' in s.text][0]
            ss: str = [ss for ss in s.text.split('\n') if 'sswApp.product' in ss][0]
            data = json.loads(ss[ss.index('=')+1:-1])
            df = to_dataframe(data)
            if not df.empty:
                dfs.append(df)

    df = pd.concat(dfs).reset_index().drop(columns='index')
    df['source'] = 'Brulerie Du Quai'
    return df
