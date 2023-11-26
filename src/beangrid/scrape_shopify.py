import asyncio
from importlib import resources as impresources
import json
import sys

from bs4 import BeautifulSoup
import httpx
import pandas as pd

import beangrid


async def scrape_shopify(base_url: str, client: httpx.AsyncClient) -> pd.DataFrame:
    url = base_url + '/products.json'

    async def get_page(page):
        data = (await client.get(url + '?page={}'.format(page))).text
        products = json.loads(data)['products']
        return products
    
    def rename_variant_dupe_cols(vdf: pd.DataFrame, prdf: pd.DataFrame):
        dupe_cols = set(vdf.columns).intersection(set(prdf.columns))
        rename_spec = {col:f"item_{col}" for col in dupe_cols}
        vdf.rename(columns=rename_spec, inplace=True)
    
    dfs = []
    page = 1
    products = await get_page(page)
    while products:
        for product in products:
            variants = product['variants']
            del product['variants']

            vdf = pd.json_normalize(variants)
            prdf = pd.json_normalize(product)
            prdf['product_url'] = base_url + '/products/' + product['handle']
            prdf['description'] = None
            if 'body_html' in prdf:
                try:
                    prdf['description'] = BeautifulSoup(prdf['body_html'], 'html.parser').text
                except Exception:
                    pass
                del prdf['body_html']
            rename_variant_dupe_cols(vdf, prdf)
            dfs.append(vdf.merge(prdf, how='cross'))

        page += 1
        products = await get_page(page)
    return pd.concat(dfs).reset_index().drop(columns='index').dropna(how='all')


async def scrape_all():
    inp_file = (impresources.files(beangrid) / 'shopify_sites.txt')
    with open(inp_file, 'r') as f:
        sites = f.read().split('\n')

    dfs = []
    async with httpx.AsyncClient() as client:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(scrape_shopify(site, client)) for site in sites if site]

    return pd.concat([task.result() for task in tasks])


async def scrape_one(site: str):
    async with httpx.AsyncClient() as client:
        df = await scrape_shopify(site, client)

    return df


if __name__ == '__main__':
    if sys.argv[1]:
        df = asyncio.run(scrape_one(sys.argv[1]))
    else:
        df = asyncio.run(scrape_all())
    print(df.to_csv())