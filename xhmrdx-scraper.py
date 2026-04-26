import aiohttp
import asyncio
import os
import pytz
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from feedgen.feed import FeedGenerator

# --- 自动获取日期配置 ---
def get_bj_date():
    """获取北京时间当天的日期字符串"""
    tz = pytz.timezone('Asia/Shanghai')
    return datetime.now(tz).strftime("%Y%m%d")

DATE = get_bj_date() 
BASE_INDEX = f"http://mrdx.cn/content/{DATE}/Page01BC.htm"

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Host': 'mrdx.cn',
}

async def fetch(url, session, referer=None):
    headers = DEFAULT_HEADERS.copy()
    if referer: headers['Referer'] = referer
    try:
        async with session.get(url, headers=headers, timeout=15) as response:
            if response.status == 200:
                raw_data = await response.read()
                return raw_data.decode('utf-8', errors='ignore')
            return ""
    except:
        return ""

async def get_article_detail(page_name, title_from_nav, article_url, page_url, session):
    html = await fetch(article_url, session, referer=page_url)
    if not html: return None
    soup = BeautifulSoup(html, 'html.parser')
    
    # 提取详情页标题逻辑
    main_title = soup.find('h2')
    sub_title = soup.find('h4')
    display_title = main_title.get_text(strip=True) if main_title else title_from_nav
    if sub_title and sub_title.get_text(strip=True):
        sub_text = sub_title.get_text(strip=True).replace('——', '').replace('<br>', '')
        display_title = f"{display_title} —— {sub_text}"

    final_title = f"[{page_name}] {display_title}"

    # 提取并清理正文
    content_area = soup.find(id="contenttext") or soup.find(id="ozoom")
    if content_area:
        for tag in content_area.find_all(['style', 'script']): tag.decompose()
        base_dir = article_url.rsplit('/', 1)[0] + '/'
        for img in content_area.find_all('img'):
            if img.get('src'): img['src'] = urljoin(base_dir, img['src'])
        return {'title': final_title, 'url': article_url, 'content_html': str(content_area)}
    return None

async def main():
    async with aiohttp.ClientSession() as session:
        print(f"🚀 自动化抓取启动 | 目标日期: {DATE}")
        index_html = await fetch(BASE_INDEX, session)
        
        if not index_html:
            print(f"⚠️ 无法获取 {DATE} 的报纸，可能尚未更新。")
            return

        soup = BeautifulSoup(index_html, 'html.parser')
        nav_div = soup.find('div', class_='listdaohang')
        if not nav_div: return

        # 1. 解析版面结构
        tasks = []
        h4_tags = nav_div.find_all('h4')
        for h4 in h4_tags:
            page_name = h4.get_text(strip=True)
            ul_tag = h4.find_next_sibling('ul')
            if ul_tag:
                links = ul_tag.find_all('a', attrs={'daoxiang': True})
                for link in links:
                    url = urljoin(BASE_INDEX, link.get('daoxiang'))
                    nav_title = link.get_text(strip=True)
                    tasks.append(get_article_detail(page_name, nav_title, url, BASE_INDEX, session))

        # 2. 修正网页源码的倒序排列
        tasks.reverse() 

        # 3. 异步并发抓取
        print(f"📦 正在下载 {len(tasks)} 篇文章...")
        results = await asyncio.gather(*tasks)
        articles = [r for r in results if r]

        # 4. 生成 RSS 文件
        fg = FeedGenerator()
        fg.title(f'新华每日电讯 - {DATE}')
        fg.link(href=BASE_INDEX, rel='alternate')
        fg.description('全量自动化顺序版')
        fg.language('zh-CN')

        for art in articles:
            fe = fg.add_entry()
            fe.title(art['title'])
            fe.link(href=art['url'])
            fe.id(art['url'])
            fe.content(art['content_html'], type='html')

        fg.rss_file('rss_mrdx.xml', pretty=True)
        print(f"✨ 成功！文件已保存至: rss_mrdx.xml")

if __name__ == '__main__':
    # 适配 Windows 本地运行
    if os.name == 'nt': 
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
