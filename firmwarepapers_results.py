import requests
import pandas as pd
import time
from datetime import datetime
import os
from scholarly import scholarly
from googlesearch import search
import random
from urllib.parse import quote_plus
import concurrent.futures
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET
from openai import OpenAI


class MultiSourceScraper:
    def __init__(self, read_papers=None, start_year=None, end_year=None):
        self.crossref_url = "https://api.crossref.org/works"
        self.headers = {
            'User-Agent': 'MultiSourceScraper/1.0 (mailto:your-email@domain.com)'  
        }
        self.read_papers = read_papers or []
        self.start_year = start_year
        self.end_year = end_year or datetime.now().year
        self.arxiv_baseurl = "http://export.arxiv.org/api/query?"

    def is_within_date_range(self, year):

        if not year:
            return True  
        try:
            year = int(year)
            if self.start_year and year < self.start_year:
                return False
            if self.end_year and year > self.end_year:
                return False
            return True
        except (ValueError, TypeError):
            return True 

    def similar(self, a, b, threshold=0.85):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() > threshold

    def is_paper_read(self, title):

        return any(self.similar(title, read_title) for read_title in self.read_papers)

    def clean_query(self, query):
        return query.replace('"', '').replace('(', '').replace(')', '')

    def search_crossref(self, query, limit=1000):
        params = {
            'query': self.clean_query(query),
            'rows': limit,
            'select': 'DOI,title,author,published-print,container-title,abstract,URL',
            'sort': 'relevance',
            'filter': 'type:journal-article'
        }
        
        if self.start_year:
            params['filter'] += f',from-pub-date:{self.start_year}'
        if self.end_year:
            params['filter'] += f',until-pub-date:{self.end_year}'
        
        try:
            response = requests.get(self.crossref_url, params=params, headers=self.headers)
            response.raise_for_status()
            results = response.json().get('message', {}).get('items', [])
            before_title_check = [{
                'title': paper.get('title', [''])[0] if paper.get('title') else '',
                'authors': '; '.join([f"{author.get('given', '')} {author.get('family', '')}" 
                                    for author in paper.get('author', [])]),
                'year': paper.get('published-print', {}).get('date-parts', [[None]])[0][0],
                'venue': paper.get('container-title', [''])[0] if paper.get('container-title') else '',
                'url': paper.get('URL', ''),
                'source': 'Crossref',
                'abstract': paper.get('abstract', ''),
                'already_read': self.is_paper_read(paper.get('title', [''])[0] if paper.get('title') else '')
            } for paper in results if self.is_within_date_range(paper.get('published-print', {}).get('date-parts', [[None]])[0][0])]
            return [{'title': each['title'], 'authors': each['authors'], 'year': each['year'], 'venue': each['venue'], 'url': each['url'], 'source': each['source'], 'abstract': each['abstract'], 'already_read': each['already_read']} for each in before_title_check if 'firmware' in each['title'].lower() or 'firmware' in each['abstract'].lower() ]
        
        except Exception as e:
            print(f"Crossref error: {e}")
            return [{''}]
        
    def search_arxiv(self, query,  start=0, max_results=1000):
        base_url = 'http://export.arxiv.org/api/query?'
        search_url = f"{base_url}search_query={query}&start={start}&max_results={max_results}"
        response = requests.get(search_url)
        if response.status_code != 200:
            raise Exception(f"API request failed with status code {response.status_code}")
        
        root = ET.fromstring(response.text)
        papers = []

        for entry in root.findall('{http://www.w3.org/2005/Atom}entry'):
            title = entry.find('{http://www.w3.org/2005/Atom}title').text
            paper = {
                'title': title,
                'authors': [author.find('{http://www.w3.org/2005/Atom}name').text for author in entry.findall('{http://www.w3.org/2005/Atom}author')],
                'summary': entry.find('{http://www.w3.org/2005/Atom}summary').text.strip(),
                'pdf_url': None,
                'source': 'ArXiv',
                'year': '',
                'venue': 'ArXiv',
                'url': '',
                'abstract': entry.find('{http://www.w3.org/2005/Atom}summary').text.strip(),
                'already_read': self.is_paper_read(title)
            }

            for link in entry.findall('{http://www.w3.org/2005/Atom}link'):
                if link.attrib.get('title') == 'pdf':
                    paper['pdf_url'] = link.attrib['href']
                    paper['url'] = link.attrib['href']
                    break
            papers.append(paper)

        return papers
        


    def search_scholar(self, query, limit=1000):
        results = []
        try:
            print(f"Searching Google Scholar for: {query}")
            search_query = scholarly.search_pubs(self.clean_query(query))
            
            # Add a longer delay before starting to reduce detection chance
            time.sleep(random.uniform(3, 5))
            
            for _ in range(limit):
                try:
                    pub = next(search_query)
                    year = pub.get('bib', {}).get('pub_year', '')
                    if not self.is_within_date_range(year):
                        continue
                        
                    title = pub.get('bib', {}).get('title', '')
                    results.append({
                        'title': title,
                        'authors': '; '.join(pub.get('bib', {}).get('author', [])),
                        'year': year,
                        'venue': pub.get('bib', {}).get('venue', ''),
                        'url': pub.get('pub_url', ''),
                        'source': 'Google Scholar',
                        'abstract': pub.get('bib', {}).get('abstract', ''),
                        'already_read': self.is_paper_read(title)
                    })
                    # Use a longer random delay between requests
                    time.sleep(random.uniform(2, 4))
                except StopIteration:
                    break
                except Exception as e:
                    print(f"Scholar item error: {e}")
                    # If we get an error, add an extra delay to avoid being blocked further
                    time.sleep(random.uniform(5, 8))
                    continue
        except Exception as e:
            print(f"Scholar error: {e}")
            print("Continuing with other sources...")
        
        return results


    def search_all_sources(self, query, limit=1000):
        all_results = []
        
        print(f"\nProcessing query: {query}")
        
        # Increase ArXiv results when using it as a fallback
        arxiv_max_results = 1000  # Increased from default 5
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_source = {
                executor.submit(self.search_crossref, query, limit): "Crossref",
                #executor.submit(self.search_scholar, query, limit): "Scholar",
                #executor.submit(self.search_arxiv, query, 0, arxiv_max_results): "ArXiv"
            }
            
            for future in concurrent.futures.as_completed(future_to_source):
                source = future_to_source[future]
                try:
                    results = future.result()
                    print(f"Found {len(results)} papers from {source}")
                    all_results.extend(results)
                except Exception as e:
                    print(f"Error in {source}: {e}")       
        return all_results
    
    def generate_keyword_search(self, user_input):
        api_key = "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"
        client = OpenAI(api_key=api_key)
        prompt = (
            "You are an AI assistant proficient in formulating search queries for academic databases. "
            "Based on the user's description of their research interest, generate a precise Cross Reference API search query using the appropriate field tags and Boolean operators. "
            "Ensure the query is optimized to retrieve relevant academic papers. Let the keyword Firmware be consistent in all the generated papers\n\n"
            f"User Description: \"{user_input}\"\n\n"
            "Cross Reference API Search Query:"
        )
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Use the appropriate model
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content.strip()
# (("firmware") AND ("vulnerabilties" OR "detection" OR "attacks" OR "exploit" OR "vulnerability" OR "security" OR "risks"))


def sanitize_filename(s):
    return s.replace(" ", "_").replace("(", "").replace(")", "").replace('"', '').replace(":", "").replace("?", "").replace("/", "_")


read_papers = [
"UVSCAN: Detecting Third-Party Component Usage Violations in IoT Firmware.",
    "BootKeeper: Validating Software Integrity Properties on Boot Firmware Images.",
    "ConFirm: Detecting Firmware Modifications in Embedded Systems using Hardware Performance Counters.",
    "Volatile Memory Forensics Acquisition Efficacy: A Comparative Study Towards Analysing Firmware-Based Rootkits.",
    "Integrity Checking of Railway Interlocking Firmware.",
    "Secure bootstrap is not enough: shoring up the trusted computing base.",
    "Security in hardware assisted virtualization for cloud computing—State of the art issues and challenges.",
    "Critical analysis of the layered and systematic approaches for understanding IoT security threats and challenges.",
    "Multi-dimensional analysis of embedded systems security.",
    "Rolling Attack: An Efficient Way to Reduce Armors of Office Automation Devices.",
    "Bayesian attack graphs for platform virtualized infrastructures in clouds.",
    "Understanding The Security of Discrete GPUs.",
    "ProvUSB: Block-level Provenance-Based Data Protection for USB Storage Devices.",
    "A Modular End-to-End Framework for Secure Firmware Updates on Embedded Systems.",
    "Fuzzing proprietary protocols of programmable controllers to find vulnerabilities that affect physical control.",
    "Survey on Enterprise Internet-of-Things systems (E-IoT): A security perspective.",
    "SCADA (Supervisory Control and Data Acquisition) systems: Vulnerability assessment and security recommendations.",
    "Reliable firmware updates for the information-centric internet of things.",
    "Practical evaluation of code injection in encrypted firmware updates.",
    "Efficient implementation of low cost and secure framework with firmware updates.",
    "Mouse trap: exploiting firmware updates in USB peripherals.",
    "Secure JTAG Implementation Using Schnorr Protocol.",
    "The Keys to the Kingdom: A deleted private key, a looming deadline, and a last chance to patch a new static root of trust into the bootloader.",
    "Your Firmware Has Arrived: A Study of Firmware Update Vulnerabilities (control flow analysis).",
    "Firmware Update Attacks and Security for IoT Devices (survey with search terms).",
    "When Firmware Modifications Attack: A Case Study of Embedded Exploitation.",
    "DisARM: Mitigating Buffer Overflow Attacks on Embedded Devices.",
    "I Can Detect You: Using Intrusion Checkers to Resist Malicious Firmware Attacks (buffer overflow, ROP/JOP).",
    "A Large-Scale Empirical Analysis of the Vulnerabilities Introduced by Third-Party Components in IoT Firmware.",
    "Firmware Attack Surface Reduction (FASR).",
    "Secure JTAG Implementation Using Schnorr Protocol.",
    "A Tool for IoT Firmware Certification.",
]


if not os.path.exists('results'):
    os.makedirs('results')

start_year = 2021 
end_year = 2025 


user_queries = [
    "firmware vulnerabilities",
    "firmware update attacks",
    "firmware integrity detection",
    "embedded firmware security"
]
unique = set()
scraper = MultiSourceScraper(read_papers, start_year, end_year)
openai_queries = [scraper.generate_keyword_search(que) for que in user_queries]

for i in range(5):
    scraper = MultiSourceScraper(read_papers, start_year, end_year)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')


    results_folder = f'results/From_{start_year}_To_{end_year}_RecordedAt_{timestamp}'
    os.makedirs(results_folder, exist_ok=True)


    date_range_info = f"Date range: "
    date_range_info += f"from {start_year} " if start_year else "no start year limit "
    date_range_info += f"to {end_year}" if end_year else "no end year limit"
    print(date_range_info)

    all_read_papers = []
    total_papers_found = 0
    total_unique_papers = 0

    print(f"\nResults will be saved to: {results_folder}")

    for i, query in enumerate(openai_queries, start=1):

        print(f"\nExecuting query {i}/{len(user_queries)}: {query}")
        
        results = scraper.search_all_sources(query)
        
        if results:
            df = pd.DataFrame(results)
            df = df.drop_duplicates(subset=['title'], keep='first')

            
            read_papers_df = df[df['already_read']]
            unread_papers_df = df[~df['already_read']]
            
            all_read_papers.extend(read_papers_df.to_dict('records'))
            
            date_suffix = f"_{start_year}-{end_year}" if start_year and end_year else ""
            filename = f'{results_folder}/all_query_{query}_papers_query_{i}{date_suffix}.csv'
            df.to_csv(filename, index=False, encoding='utf-8')
            
            for source in df['source'].unique():
                source_df = df[df['source'] == source]
            
                source_filename = f'{results_folder}/read_query_{query}_papers_query_{i}_{source.lower().replace(" ", "_")}{date_suffix}.csv'
                source_df.to_csv(source_filename, index=False, encoding='utf-8')
            
            unread_filename = f'{results_folder}/unread_query_{query}_papers_query_{i}_unread{date_suffix}.csv'
            unread_papers_df.to_csv(unread_filename, index=False, encoding='utf-8')
            
            total_papers_found += len(results)
            total_unique_papers += len(df)
            
            print(f"Found {len(df)} unique papers for query {i}")
            print(f"  - {len(read_papers_df)} already read papers")
            print(f"  - {len(unread_papers_df)} new unread papers")
            
            if not unread_papers_df.empty:
                print("\nSample of new papers found:")
                for idx, paper in unread_papers_df.head(3).iterrows():
                    print(f"  - {paper['title']} ({paper['source']})")
        
        else:
            
            print(f"No results found for Query {i}")
        
        time.sleep(2)

    print(f"\nSearch complete! Found {total_papers_found} total papers ({total_unique_papers} unique)")
    print(f"Results saved to: {results_folder}")

    
    start_year -= 5
    end_year -= 5
