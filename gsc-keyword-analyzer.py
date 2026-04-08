#!/usr/bin/env python3
"""
GSC Keyword Analyzer - Analyzes Google Search Console data to identify SEO opportunities.

Usage:
    python3 gsc-keyword-analyzer.py --queries /path/to/queries.csv --pages /path/to/pages.csv

Features:
- Identifies keywords with high impressions but low CTR (meta optimization needed)
- Finds keywords ranking on page 2 that can be pushed to page 1
- Detects pages with CTR problems (good position, low clicks)
- Provides prioritized action items
"""

import argparse
import csv
import sys
from dataclasses import dataclass
from typing import List


@dataclass
class QueryData:
    query: str
    clicks: int
    impressions: int
    ctr: float
    position: float

    @property
    def opportunity_score(self) -> float:
        """Calculate opportunity score: higher is better opportunity."""
        score = 0
        score += self.impressions * 2
        if self.position <= 10 and self.ctr < 5:
            score += 100
        if 10 < self.position <= 20:
            score += 50 + self.impressions
        if 20 < self.position <= 30 and self.impressions > 10:
            score += 30 + self.impressions
        return score


@dataclass
class PageData:
    url: str
    clicks: int
    impressions: int
    ctr: float
    position: float

    @property
    def ctr_problem(self) -> bool:
        """Page has good position but low CTR."""
        return self.position <= 10 and self.ctr < 3 and self.impressions >= 10


def parse_percentage(value: str) -> float:
    """Parse percentage string like '23.08%' to float 23.08."""
    try:
        return float(value.replace('%', '').replace(',', '.'))
    except:
        return 0.0


def parse_number(value: str) -> float:
    """Parse number string, handling commas as decimal separators."""
    try:
        return float(value.replace(',', '.'))
    except:
        return 0.0


def read_queries_csv(filepath: str) -> List[QueryData]:
    """Read and parse queries CSV file."""
    queries = []
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header

        for row in reader:
            if len(row) >= 5:
                queries.append(QueryData(
                    query=row[0].strip(),
                    clicks=int(parse_number(row[1])),
                    impressions=int(parse_number(row[2])),
                    ctr=parse_percentage(row[3]),
                    position=parse_number(row[4])
                ))
    return queries


def read_pages_csv(filepath: str) -> List[PageData]:
    """Read and parse pages CSV file."""
    pages = []
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header

        for row in reader:
            if len(row) >= 5:
                pages.append(PageData(
                    url=row[0].strip(),
                    clicks=int(parse_number(row[1])),
                    impressions=int(parse_number(row[2])),
                    ctr=parse_percentage(row[3]),
                    position=parse_number(row[4])
                ))
    return pages


def analyze_queries(queries: List[QueryData]) -> dict:
    """Analyze queries and categorize opportunities."""
    analysis = {
        'ctr_optimization': [],
        'push_to_page1': [],
        'content_improvement': [],
        'winning_keywords': [],
        'quick_wins': [],
    }

    for q in queries:
        if q.position <= 10 and q.ctr < 5 and q.impressions >= 5:
            analysis['ctr_optimization'].append(q)
        if 10 < q.position <= 20 and q.impressions >= 5:
            analysis['push_to_page1'].append(q)
        if 20 < q.position <= 40 and q.impressions >= 10:
            analysis['content_improvement'].append(q)
        if q.clicks > 0 and q.ctr > 10:
            analysis['winning_keywords'].append(q)
        if 5 <= q.position <= 15 and q.impressions >= 10:
            analysis['quick_wins'].append(q)

    for key in analysis:
        analysis[key].sort(key=lambda x: x.opportunity_score, reverse=True)

    return analysis


def analyze_pages(pages: List[PageData]) -> dict:
    """Analyze pages for issues."""
    analysis = {
        'ctr_problems': [],
        'high_impression': [],
        'top_performers': [],
    }

    for p in pages:
        if p.ctr_problem:
            analysis['ctr_problems'].append(p)
        if p.impressions >= 20 and p.clicks == 0:
            analysis['high_impression'].append(p)
        if p.clicks > 0:
            analysis['top_performers'].append(p)

    analysis['ctr_problems'].sort(key=lambda x: x.impressions, reverse=True)
    analysis['high_impression'].sort(key=lambda x: x.impressions, reverse=True)
    analysis['top_performers'].sort(key=lambda x: x.clicks, reverse=True)

    return analysis


def print_report(query_analysis: dict, page_analysis: dict):
    """Print comprehensive SEO report."""

    print("=" * 70)
    print("                    GSC SEO OPPORTUNITY REPORT")
    print("=" * 70)

    # Priority 1: CTR Optimization
    print("\n[PRIORITY 1] CTR OPTIMIZATION (Meta Title/Description)")
    print("-" * 70)
    print("These keywords rank well but get no clicks. Fix meta tags!\n")

    if query_analysis['ctr_optimization']:
        print(f"{'Keyword':<45} {'Impr':>8} {'Pos':>6} {'CTR':>6}")
        print("-" * 70)
        for q in query_analysis['ctr_optimization'][:10]:
            print(f"{q.query[:44]:<45} {q.impressions:>8} {q.position:>6.1f} {q.ctr:>5.1f}%")
    else:
        print("No CTR optimization opportunities found.")

    # Priority 2: Push to Page 1
    print("\n\n[PRIORITY 2] PUSH TO PAGE 1 (Position 11-20)")
    print("-" * 70)
    print("These keywords are close to page 1. Small improvements = big gains!\n")

    if query_analysis['push_to_page1']:
        print(f"{'Keyword':<45} {'Impr':>8} {'Pos':>6} {'CTR':>6}")
        print("-" * 70)
        for q in query_analysis['push_to_page1'][:10]:
            print(f"{q.query[:44]:<45} {q.impressions:>8} {q.position:>6.1f} {q.ctr:>5.1f}%")
    else:
        print("No page 2 keywords found.")

    # Priority 3: Content Improvement
    print("\n\n[PRIORITY 3] CONTENT IMPROVEMENT NEEDED (Position 20-40)")
    print("-" * 70)
    print("Create or improve content targeting these keywords.\n")

    if query_analysis['content_improvement']:
        print(f"{'Keyword':<45} {'Impr':>8} {'Pos':>6}")
        print("-" * 70)
        for q in query_analysis['content_improvement'][:10]:
            print(f"{q.query[:44]:<45} {q.impressions:>8} {q.position:>6.1f}")
    else:
        print("No content improvement opportunities found.")

    # Page CTR Problems
    print("\n\n[ALERT] PAGES WITH CTR PROBLEMS (Good Position, No Clicks)")
    print("-" * 70)
    print("These pages rank well but don't get clicks. Fix meta tags!\n")

    all_problem_pages = page_analysis['ctr_problems'] + page_analysis['high_impression']
    if all_problem_pages:
        all_problem_pages.sort(key=lambda x: x.impressions, reverse=True)
        seen = set()
        for p in all_problem_pages[:10]:
            if p.url in seen:
                continue
            seen.add(p.url)
            slug = p.url.split('/')[-2] if p.url.endswith('/') else p.url.split('/')[-1]
            print(f"* {slug[:55]}")
            print(f"  Impressions: {p.impressions}, Position: {p.position:.1f}, CTR: {p.ctr:.1f}%")
            print()
    else:
        print("No page CTR problems found.")

    # Winning Keywords
    print("\n[SUCCESS] TOP PERFORMING KEYWORDS (Keep Optimizing)")
    print("-" * 70)

    if query_analysis['winning_keywords']:
        print(f"{'Keyword':<40} {'Clicks':>7} {'Impr':>7} {'CTR':>6} {'Pos':>6}")
        print("-" * 70)
        for q in query_analysis['winning_keywords'][:10]:
            print(f"{q.query[:39]:<40} {q.clicks:>7} {q.impressions:>7} {q.ctr:>5.1f}% {q.position:>6.1f}")
    else:
        print("No winning keywords found yet.")

    # Summary
    print("\n" + "=" * 70)
    print("                         ACTION SUMMARY")
    print("=" * 70)
    print(f"""
1. CTR Optimization: {len(query_analysis['ctr_optimization'])} keywords need meta tag improvements
2. Push to Page 1:   {len(query_analysis['push_to_page1'])} keywords are on page 2
3. Content Needed:   {len(query_analysis['content_improvement'])} keywords need new/better content
4. Page CTR Issues:  {len(page_analysis['ctr_problems']) + len(page_analysis['high_impression'])} pages have CTR problems

Quick Wins: {len(query_analysis['quick_wins'])} keywords in positions 5-15 with traffic potential
""")


def export_csv(query_analysis: dict, page_analysis: dict, output_path: str):
    """Export analysis to CSV for further processing."""
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)

        writer.writerow(['Category', 'Query/URL', 'Clicks', 'Impressions', 'CTR', 'Position', 'Priority'])

        for q in query_analysis['ctr_optimization']:
            writer.writerow(['CTR_Optimization', q.query, q.clicks, q.impressions, q.ctr, q.position, 'HIGH'])

        for q in query_analysis['push_to_page1']:
            writer.writerow(['Push_to_Page1', q.query, q.clicks, q.impressions, q.ctr, q.position, 'MEDIUM'])

        for q in query_analysis['content_improvement']:
            writer.writerow(['Content_Needed', q.query, q.clicks, q.impressions, q.ctr, q.position, 'MEDIUM'])

        all_pages = page_analysis['ctr_problems'] + page_analysis['high_impression']
        for p in all_pages:
            writer.writerow(['Page_CTR_Problem', p.url, p.clicks, p.impressions, p.ctr, p.position, 'HIGH'])


def main():
    parser = argparse.ArgumentParser(
        description='Analyze GSC data for SEO opportunities',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 gsc-keyword-analyzer.py -q queries.csv -p pages.csv
  python3 gsc-keyword-analyzer.py -q queries.csv -p pages.csv -o report.txt
  python3 gsc-keyword-analyzer.py -q queries.csv -p pages.csv --csv analysis.csv
        """
    )
    parser.add_argument('--queries', '-q', required=True, help='Path to queries CSV file')
    parser.add_argument('--pages', '-p', required=True, help='Path to pages CSV file')
    parser.add_argument('--output', '-o', help='Save report to text file')
    parser.add_argument('--csv', help='Export analysis to CSV file')

    args = parser.parse_args()

    try:
        queries = read_queries_csv(args.queries)
        pages = read_pages_csv(args.pages)
        print(f"Loaded {len(queries)} queries and {len(pages)} pages\n")
    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading files: {e}")
        sys.exit(1)

    query_analysis = analyze_queries(queries)
    page_analysis = analyze_pages(pages)

    if args.output:
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            print_report(query_analysis, page_analysis)
        output = f.getvalue()

        with open(args.output, 'w', encoding='utf-8') as out:
            out.write(output)
        print(f"Report saved to {args.output}")
    else:
        print_report(query_analysis, page_analysis)

    if args.csv:
        export_csv(query_analysis, page_analysis, args.csv)
        print(f"\nCSV export saved to {args.csv}")


if __name__ == '__main__':
    main()
