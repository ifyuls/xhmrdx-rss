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
    tz = pytz.timezone('Asia/Shanghai')
    return datetime.now(tz).strftime("%Y%m%d")

DATE = get_bj_date()
BASE_INDEX = f"http://mrdx.cn/content/{DATE}/Page01BC.htm"

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Host': 'mrdx.cn',
}

CONNECTOR = aiohttp.TCPConnector(limit_per_host=5, limit=10)

async def fetch(url, session, referer=None):
    headers = DEFAULT_HEADERS.copy()
    if referer:
        headers['Referer'] = referer
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
    """
    抓取文章详情，无论成功或失败都返回一个字典，保证 RSS 中每篇文章都有条目。
    返回格式：{'title': str, 'url': str, 'content_html': str, 'success': bool}
    """
    max_retries = 3
    html = ""
    for attempt in range(max_retries):
        html = await fetch(article_url, session, referer=page_url)
        if html:
            break
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # 1, 2, 4 秒
            print(f"⏳ {article_url} 第 {attempt+1} 次重试，等待 {wait_time} 秒...")
            await asyncio.sleep(wait_time)

    # 最终失败的情况：无法获取 HTML
    if not html:
        print(f"⚠️ 最终失败（网络/重试）: {article_url}")
        return {
            'title': f"[{page_name}] {title_from_nav}",
            'url': article_url,
            'content_html': '<p>抓取失败，请点击链接查看原文。</p>',
            'success': False
        }

    soup = BeautifulSoup(html, 'html.parser')

    # 提取标题（优先使用页面内的 h2，否则使用导航标题）
    main_title = soup.find('h2')
    sub_title = soup.find('h4')
    display_title = main_title.get_text(strip=True) if main_title else title_from_nav
    if sub_title and sub_title.get_text(strip=True):
        sub_text = sub_title.get_text(strip=True).replace('——', '').replace('<br>', '')
        display_title = f"{display_title} —— {sub_text}"

    final_title = f"[{page_name}] {display_title}"

    # 提取正文
    content_area = soup.find(id="contenttext") or soup.find(id="ozoom")
    if content_area:
        for tag in content_area.find_all(['style', 'script']):
            tag.decompose()
        base_dir = article_url.rsplit('/', 1)[0] + '/'
        for img in content_area.find_all('img'):
            if img.get('src'):
                img['src'] = urljoin(base_dir, img['src'])

        print(f"✅ 成功抓取: {final_title}")
        return {
            'title': final_title,
            'url': article_url,
            'content_html': str(content_area),
            'success': True
        }
    else:
        # 拿到了 HTML，但找不到正文容器
        print(f"❌ 无法解析正文（未找到 contenttext/ozoom）: {article_url} | 标题: {final_title}")
        return {
            'title': final_title,
            'url': article_url,
            'content_html': '<p>正文解析失败，请点击链接查看原文。</p>',
            'success': False
        }

async def main():
    async with aiohttp.ClientSession(connector=CONNECTOR) as session:
        print(f"🚀 自动化抓取启动 | 目标日期: {DATE}")
        print(f"🔗 正在请求首页: {BASE_INDEX}")
        index_html = await fetch(BASE_INDEX, session)

        if not index_html:
            print(f"🛑 错误: 无法获取 {DATE} 的报纸首页，程序退出。")
            return

        soup = BeautifulSoup(index_html, 'html.parser')
        nav_div = soup.find('div', class_='listdaohang')
        if not nav_div:
            print("🛑 错误: 首页解析失败，未找到 'listdaohang' 标签。")
            return

        # 按原始顺序收集任务
        tasks = []
        h4_tags = nav_div.find_all('h4')
        print(f"📊 首页解析成功，发现 {len(h4_tags)} 个版面。")

        for h4 in h4_tags:
            page_name = h4.get_text(strip=True)
            ul_tag = h4.find_next_sibling('ul')
            if ul_tag:
                links = ul_tag.find_all('a', attrs={'daoxiang': True})
                for link in links:
                    url = urljoin(BASE_INDEX, link.get('daoxiang'))
                    nav_title = link.get_text(strip=True)
                    tasks.append(
                        get_article_detail(page_name, nav_title, url, BASE_INDEX, session)
                    )

        total_links = len(tasks)
        print(f"📦 总文章链接数: {total_links} 条。开始抓取（保留顺序，失败也会生成占位条目）...")

        # 控制并发，保持顺序
        semaphore = asyncio.Semaphore(5)

        async def limited_task(task):
            async with semaphore:
                result = await task
                await asyncio.sleep(0.5)  # 请求间隔
                return result

        results = await asyncio.gather(*[limited_task(task) for task in tasks])
        # results 顺序与 tasks 完全一致，每个元素都是一个字典（含 success 字段）

        success_count = sum(1 for r in results if r['success'])
        fail_count = total_links - success_count
        print(f"抓取完成。成功 {success_count} 篇，失败 {fail_count} 篇。")
        if fail_count > 0:
            print("失败链接已在上方日志中标记（❌ 或 ⚠️）。")

        # 生成 RSS（包含所有条目，顺序不变）
        fg = FeedGenerator()
        fg.title(f'新华每日电讯 - {DATE}')
        fg.link(href=BASE_INDEX, rel='alternate')
        fg.description('全量自动化顺序版（含失败条目占位）')
        fg.language('zh-CN')

        for art in results:
            fe = fg.add_entry()
            fe.title(art['title'])
            fe.link(href=art['url'])
            fe.id(f"{art['url']}#{art['title']}")
            fe.content(art['content_html'], type='html')

        fg.rss_file('rss_mrdx.xml', pretty=True)
        print(f"✨ RSS 文件已生成: rss_mrdx.xml （共 {len(results)} 条，按原始顺序排列）")

if __name__ == '__main__':
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
