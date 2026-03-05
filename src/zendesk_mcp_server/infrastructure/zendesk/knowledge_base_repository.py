from __future__ import annotations

from typing import Any, Dict


class KnowledgeBaseRepository:
    def __init__(self, *, zenpy_client) -> None:
        self._zenpy_client = zenpy_client

    def get_all_articles(self) -> Dict[str, Any]:
        sections = self._zenpy_client.help_center.sections()

        knowledge_base = {}
        for section in sections:
            articles = self._zenpy_client.help_center.sections.articles(section.id)
            knowledge_base[section.name] = {
                "section_id": section.id,
                "description": section.description,
                "articles": [
                    {
                        "id": article.id,
                        "title": article.title,
                        "body": article.body,
                        "updated_at": str(article.updated_at),
                        "url": article.html_url,
                    }
                    for article in articles
                ],
            }

        return knowledge_base
