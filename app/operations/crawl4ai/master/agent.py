import asyncio
import random
import time

from typing import Set

from gpt_researcher.config import Config
from gpt_researcher.context.compression import ContextCompressor, WrittenContentCompressor
from gpt_researcher.document import DocumentLoader, LangChainDocumentLoader
from operations.crawl4ai.master.actions import *
from gpt_researcher.memory import Memory
from gpt_researcher.utils.enum import ReportSource, ReportType, Tone


class GPTResearcher:
    """
    GPT Researcher
    """

    def __init__(
        self,
        query: str,
        report_type: str = ReportType.ResearchReport.value,
        report_source=ReportSource.Web.value,
        tone: Tone = Tone.Objective,
        source_urls=None,
        documents=None,
        config_path=None,
        websocket=None,
        agent=None,
        role=None,
        parent_query: str = "",
        subtopics: list = [],
        visited_urls: set = set(),
        verbose: bool = True,
        context=[],
        headers: dict = None,  # Add headers parameter
    ):
        """
        Initialize the GPT Researcher class.
        Args:
            query: str,
            report_type: str
            source_urls
            tone
            config_path
            websocket
            agent
            role
            parent_query: str
            subtopics: list
            visited_urls: set
        """
        self.headers = headers or {}
        self.query: str = query
        self.cfg = Config(config_path)
        self.retrievers = get_retrievers(self.headers, self.cfg)
        # Ensure tone is an instance of Tone enum
        if isinstance(tone, dict):
            print(f"Invalid tone format: {tone}. Setting to default Tone.Objective.")
            self.tone = Tone.Objective
        elif isinstance(tone, str):
            self.tone = Tone[tone]
        else:
            self.tone = tone

        # Only relevant for DETAILED REPORTS
        # --------------------------------------

        # Stores the main query of the detailed report
        self.parent_query = parent_query

        # Stores all the user provided subtopics
        self.subtopics = subtopics




    async def __get_context_by_search(self, query, scraped_data: list = []):
        """
           Generates the context for the research task by searching the query and scraping the results
        Returns:
            context: List of context
        """
        context = []
        # Generate Sub-Queries including original query
        sub_queries = await get_sub_queries(
            query=query,
            agent_role_prompt=self.role,
            cfg=self.cfg,
            parent_query=self.parent_query,
            report_type=self.report_type,
            cost_callback=self.add_costs,
            openai_api_key=self.headers.get("openai_api_key"),
        )

        # If this is not part of a sub researcher, add original query to research for better results
        if self.report_type != "subtopic_report":
            sub_queries.append(query)

        if self.verbose:
            await stream_output(
                "logs",
                "subqueries",
                f"🗂️ I will conduct my research based on the following queries: {sub_queries}...",
                self.websocket,
                True,
                sub_queries,
            )

        # Using asyncio.gather to process the sub_queries asynchronously
        context = await asyncio.gather(
            *[
                self.__process_sub_query(sub_query, scraped_data)
                for sub_query in sub_queries
            ]
        )
        return context

    async def __scrape_data_by_query(self, sub_query):
        """
        Runs a sub-query across multiple retrievers and scrapes the resulting URLs.

        Args:
            sub_query (str): The sub-query to search for.

        Returns:
            list: A list of scraped content results.
        """
        new_search_urls = []

        # Iterate through all retrievers
        for retriever_class in self.retrievers:
            # Instantiate the retriever with the sub-query
            retriever = retriever_class(sub_query)

            # Perform the search using the current retriever
            search_results = await asyncio.to_thread(
                retriever.search, max_results=self.cfg.max_search_results_per_query
            )

            # Collect new URLs from search results
            search_urls = [url.get("href") for url in search_results]
            new_search_urls.extend(search_urls)

        # Get unique URLs
        new_search_urls = await self.__get_new_urls(new_search_urls)
        random.shuffle(new_search_urls)

        # Scrape the new URLs
        scraped_content_results = await asyncio.to_thread(
            scrape_urls, new_search_urls, self.cfg
        )

        return scraped_content_results

