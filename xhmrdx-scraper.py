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
            else:
                print(f"❌ 网页请求失败 [状态码 {response.status}]: {url}")
                return ""
    except Exception as e:
        print(f"❌ 网络请求异常 [{type(e).__name__}]: {url}")
        return ""

async def get_article_detail(page_name, title_from_nav, article_url, page_url, session):
    html = await fetch(article_url, session, referer=page_url)
    if not html:
        print(f"⚠️ 跳过解析（获取HTML为空）: {article_url}")
        return None
        
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
            
        # 成功日志
        print(f"✅ 成功抓取: {final_title}")
        return {'title': final_title, 'url': article_url, 'content_html': str(content_area)}
    
    # 失败日志：找到了网页，但找不到正文标签
    print(f"❌ 无法解析正文（未找到 contenttext/ozoom 标签）: {article_url} | 标题: {final_title}")
    return None

async def main():
    async with aiohttp.ClientSession() as session:
        print(f"🚀 自动化抓取启动 | 目标日期: {DATE}")
        print(f"🔗 正在请求首页: {BASE_INDEX}")
        index_html = await fetch(BASE_INDEX, session)
        
        if not index_html:
            print(f"🛑 错误: 无法获取 {DATE} 的报纸首页，可能尚未更新或网络阻断。")
            return

        soup = BeautifulSoup(index_html, 'html.parser')
        nav_div = soup.find('div', class_='listdaohang')
        if not nav_div: 
            print("🛑 错误: 首页解析失败，未找到 'listdaohang' 标签，网站结构可能已发生变化！")
            return

        # 1. 解析版面结构
        tasks = []
        h4_tags = nav_div.find_all('h4')
        print(f"📊 首页解析成功，发现 {len(h4_tags)} 个版面。开始提取文章链接...")
        
        for h4 in h4_tags:
            page_name = h4.get_text(strip=True)
            ul_tag = h4.find_next_sibling('ul')
            if ul_tag:
                links = ul_tag.find_all('a', attrs={'daoxiang': True})
                for link in links:
                    url = urljoin(BASE_INDEX, link.get('daoxiang'))
                    nav_title = link.get_text(strip=True)
                    tasks.append(get_article_detail(page_name, nav_title, url, BASE_INDEX, session))

        total_parsed_links = len(tasks)
        print(f"📦 发现总文章链接数: {total_parsed_links} 条。开始并发下载...")

        # 2. 修正网页源码的倒序排列
        tasks.reverse() 

        # 3. 异步并发抓取
        results = await asyncio.gather(*tasks)
        articles = [r for r in results if r]
        
        print(f"完成并发下载。发现链接 {total_parsed_links} 条，成功解析正文 {len(articles)} 篇。")

        # 4. 生成 RSS 文件
        fg = FeedGenerator()
        fg.title(f'新华每日电讯 - {DATE}')
        fg.link(href=BASE_INDEX, rel='alternate')
        fg.description('全量自动化顺序版')
        fg.language('zh-CN')

        rss_count = 0
        for art in articles:
            fe = fg.add_entry()
            fe.title(art['title'])
            fe.link(href=art['url'])
            
            # 【重要修改】使用 URL 结合 标题 作为唯一ID，防止相同URL覆盖
            unique_id = f"{art['url']}#{art['title']}"
            fe.id(unique_id)
            
            fe.content(art['content_html'], type='html')
            rss_count += 1

        print(f"📝 正在写入 RSS 文件，共 {rss_count} 条 Item...")
        fg.rss_file('rss_mrdx.xml', pretty=True)
        print(f"✨ 成功！文件已保存至: rss_mrdx.xml")

if __name__ == '__main__':
    if os.name == 'nt': 
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
