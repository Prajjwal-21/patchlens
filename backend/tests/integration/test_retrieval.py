"""Retrieval tests: ranking primitives, fusion, reranking and the strategy sweep.

The primitives are tested directly (deterministic, no database). The strategy sweep
is an integration test over a real indexed fixture repository, because the claim
"there are five comparable strategies" is only meaningful against real data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from patchlens.core.config import Settings
from patchlens.core.enums import RetrievalStrategy
from patchlens.services.providers.factory import build_embedding_provider
from patchlens.services.retrieval import (
    BM25Index,
    CodeRetriever,
    HeuristicReranker,
    reciprocal_rank_fusion,
)
from patchlens.services.retrieval.base import RetrievedSymbol
from patchlens.services.retrieval.corpus import Corpus, CorpusDocument
from patchlens.services.retrieval.rerank import build_reranker
from tests.conftest import index_repository, register_repository


class TestBM25:
    def test_ranks_the_matching_document_first(self) -> None:
        index = BM25Index(
            [
                ["verify", "github", "webhook", "signature", "hmac"],
                ["render", "settings", "page", "tailwind"],
                ["parse", "unified", "diff", "hunk"],
            ]
        )
        ranked = index.score(["webhook", "signature"])
        assert ranked
        assert ranked[0].index == 0

    def test_rare_terms_weigh_more_than_common_ones(self) -> None:
        index = BM25Index([["auth", "helper"], ["auth", "guard"], ["auth", "token"]])
        common = index.idf("auth")
        rare = index.idf("token")
        assert rare > common

    def test_unknown_terms_score_nothing(self) -> None:
        index = BM25Index([["alpha", "beta"]])
        assert index.score(["zzz"]) == []

    def test_longer_documents_are_penalised(self) -> None:
        index = BM25Index([["alpha"], ["alpha"] + ["filler"] * 40])
        ranked = index.score(["alpha"])
        assert [item.index for item in ranked] == [0, 1]
        assert ranked[0].score > ranked[1].score

    def test_empty_corpus_and_empty_query_are_safe(self) -> None:
        assert BM25Index([]).score(["anything"]) == []
        assert BM25Index([["alpha"]]).score([]) == []

    def test_invalid_parameters_are_refused(self) -> None:
        with pytest.raises(ValueError):
            BM25Index([["a"]], k1=0.0)
        with pytest.raises(ValueError):
            BM25Index([["a"]], b=1.5)


class TestReciprocalRankFusion:
    def test_document_ranked_by_both_retrievers_wins(self) -> None:
        fused = reciprocal_rank_fusion(
            {"lexical": [("s1", "a.py"), ("s2", "b.py")], "semantic": [("s2", "b.py")]},
            k=60,
        )
        by_key = {item.key: item.fused_score for item in fused}
        assert by_key[("s2", "b.py")] > by_key[("s1", "a.py")]
        assert fused[0].key == ("s2", "b.py")
        assert fused[0].ranks == {"lexical": 2, "semantic": 1}
        assert fused[0].components["semantic"] == pytest.approx(1 / 61)

    def test_contributions_are_recorded_per_retriever(self) -> None:
        fused = reciprocal_rank_fusion({"lexical": [("s1", "a.py")], "semantic": [("s1", "a.py")]})
        assert fused[0].components == pytest.approx({"lexical": 1 / 61, "semantic": 1 / 61})
        assert fused[0].fused_score == pytest.approx(2 / 61)

    def test_zero_weight_disables_a_retriever(self) -> None:
        fused = reciprocal_rank_fusion(
            {"lexical": [("s1", "a.py")], "semantic": [("s2", "b.py")]},
            weights={"semantic": 0.0},
        )
        assert [item.key for item in fused] == [("s1", "a.py")]
        assert fused[0].components == {"lexical": pytest.approx(1 / 61)}

    def test_invalid_k_is_refused(self) -> None:
        with pytest.raises(ValueError):
            reciprocal_rank_fusion({}, k=0)


def document(
    symbol_id: str, name: str, *, is_test: bool = False, docstring: str | None = None
) -> CorpusDocument:
    words = name.lower().split("_")
    return CorpusDocument(
        symbol_id=symbol_id,
        path="tests/test_x.py" if is_test else "pkg/x.py",
        qualified_name=name,
        kind="function",
        name=name,
        start_line=1,
        end_line=3,
        is_test=is_test,
        signature=f"def {name}()",
        docstring=docstring,
        source=f"def {name}():\n    return 1\n",
        text=f"def {name}(): return 1",
        tokens=["def", *words, "return"],
    )


def hit(doc: CorpusDocument, *, score: float) -> RetrievedSymbol:
    return RetrievedSymbol(
        symbol_id=doc.symbol_id,
        path=doc.path,
        qualified_name=doc.qualified_name,
        kind=doc.kind,
        start_line=doc.start_line,
        end_line=doc.end_line,
        score=score,
        retriever="hybrid",
        is_test=doc.is_test,
        docstring=doc.docstring,
        signature=doc.signature,
    )


class TestHeuristicReranker:
    def test_documented_weights_decide_not_a_hidden_model(self) -> None:
        reranker = HeuristicReranker()
        on_topic = document("s1", "render_settings_page", docstring="Render the settings page.")
        off_topic = document("s2", "parse_unified_diff")
        documents = {item.symbol_id: item for item in (on_topic, off_topic)}
        candidates = [
            hit(on_topic, score=5.0),
            hit(off_topic, score=4.0),
        ]
        reranked = reranker.rerank(
            query="render settings page", candidates=candidates, documents=documents, limit=10
        )
        assert [item.symbol_id for item in reranked] == ["s1", "s2"], (
            "name/topic match must overtake a raw score"
        )
        assert reranked[0].components["rerank"] > 0
        assert any("rerank" in reason for reason in reranked[0].reasons)

    def test_blend_keeps_recall_and_stays_bounded(self) -> None:
        reranker = HeuristicReranker()
        doc = document("s1", "render_settings_page")
        reranked = reranker.rerank(
            query="render settings page",
            candidates=[hit(doc, score=0.9)],
            documents={"s1": doc},
            limit=10,
        )
        # 0.5 * fusion score + 0.5 * feature score, clamped into [0, 1].
        assert 0.0 <= reranked[0].score <= 1.0

    def test_related_tests_are_not_ranked_first_unless_the_query_mentions_tests(self) -> None:
        reranker = HeuristicReranker()
        test_doc = document("t1", "test_parse_unified_diff", is_test=True)
        source_doc = document("s1", "parse_unified_diff")
        documents = {"t1": test_doc, "s1": source_doc}

        # Reranking reads and rewrites the candidate objects, so each call gets
        # fresh ones - exactly as the retrievers build fresh results per query.
        without_mention = reranker.rerank(
            query="parse unified diff",
            candidates=[hit(test_doc, score=1.0), hit(source_doc, score=1.0)],
            documents=documents,
            limit=10,
        )
        assert without_mention[0].symbol_id == "s1"

        for_test = reranker.rerank(
            query="which test covers parse unified diff",
            candidates=[hit(test_doc, score=1.0), hit(source_doc, score=1.0)],
            documents=documents,
            limit=10,
        )
        for_test_ids = [item.symbol_id for item in for_test]
        assert for_test_ids[0] == "t1", "an explicitly test-seeking query should rank the test first"

    def test_kind_prior_is_what_penalises_tests_when_they_were_not_asked_for(self) -> None:
        from patchlens.services.embedding.hashing import tokenise

        reranker = HeuristicReranker()
        test_doc = document("t1", "test_parse_unified_diff", is_test=True)
        terms = set(tokenise("parse unified diff"))
        not_asked = reranker.features(
            query_terms=terms, query_bigrams=set(), document=test_doc, mentions_test=False
        )
        asked = reranker.features(
            query_terms=terms, query_bigrams=set(), document=test_doc, mentions_test=True
        )
        assert not_asked.kind_prior == 0.0
        assert asked.kind_prior == 1.0
        assert asked.score > not_asked.score

    def test_factory_returns_the_configured_reranker_by_name(self) -> None:
        assert build_reranker("none").name == "none"
        assert build_reranker("heuristic").name == "heuristic"
        assert build_reranker(Settings(environment="test").reranker).name == "heuristic"
        assert HeuristicReranker().describe()["is_trained_cross_encoder"] is False
        with pytest.raises(ValueError):
            build_reranker("a-trained-cross-encoder-we-do-not-have")


class TestStrategySweep:
    """Integration: index the fixture repo, then compare all five strategies."""

    @pytest.fixture()
    def indexed(self, session, app_settings, demo_repo: Path):
        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings)
        session.commit()
        return repository

    def test_every_strategy_returns_ranked_results(self, session, app_settings, indexed) -> None:
        retriever = CodeRetriever(
            session, settings=app_settings, embedding_provider=build_embedding_provider(app_settings)
        )
        queries = ["add two numbers", "calculator divide", "read config"]
        for strategy in RetrievalStrategy:
            bundle = retriever.retrieve(repository=indexed, queries=queries, strategy=strategy, top_k=5)
            assert bundle.result.strategy == strategy
            assert bundle.result.results, f"{strategy} returned nothing for a populated repository"
            scores = [item.score for item in bundle.result.results]
            assert scores == sorted(scores, reverse=True)
            assert bundle.result.latency_ms >= 0.0
            assert bundle.result.backend

    def test_symbol_name_match_is_retrieved_by_lexical_search(self, session, app_settings, indexed) -> None:
        retriever = CodeRetriever(
            session, settings=app_settings, embedding_provider=build_embedding_provider(app_settings)
        )
        bundle = retriever.retrieve(
            repository=indexed, queries=["divide"], strategy=RetrievalStrategy.BM25, top_k=5
        )
        names = [item.qualified_name for item in bundle.result.results]
        assert any("divide" in name for name in names)

    def test_excluded_symbols_never_come_back(self, session, app_settings, indexed) -> None:
        retriever = CodeRetriever(
            session, settings=app_settings, embedding_provider=build_embedding_provider(app_settings)
        )
        baseline = retriever.retrieve(
            repository=indexed, queries=["add"], strategy=RetrievalStrategy.BM25, top_k=10
        )
        excluded_id = baseline.result.results[0].symbol_id
        restricted = retriever.retrieve(
            repository=indexed,
            queries=["add"],
            strategy=RetrievalStrategy.BM25,
            top_k=10,
            exclude_symbol_ids=frozenset({excluded_id}),
        )
        assert excluded_id not in {item.symbol_id for item in restricted.result.results}

    def test_hybrid_results_carry_per_retriever_components(self, session, app_settings, indexed) -> None:
        retriever = CodeRetriever(
            session, settings=app_settings, embedding_provider=build_embedding_provider(app_settings)
        )
        bundle = retriever.retrieve(
            repository=indexed, queries=["divide"], strategy=RetrievalStrategy.HYBRID, top_k=5
        )
        assert bundle.result.results
        assert any(item.components for item in bundle.result.results)

    def test_retrieval_of_an_unindexed_repository_reports_why(self, session, app_settings, demo_repo) -> None:
        repository = register_repository(session, demo_repo, full_name="patchlens/unindexed")
        retriever = CodeRetriever(
            session, settings=app_settings, embedding_provider=build_embedding_provider(app_settings)
        )
        bundle = retriever.retrieve(repository=repository, queries=["anything"])
        assert bundle.result.results == []
        assert bundle.warnings and "index" in bundle.warnings[0].lower()
        assert bundle.version_id is None

    def test_related_tests_are_returned_separately_from_context(
        self, session, app_settings, demo_repo
    ) -> None:
        repository = register_repository(session, demo_repo)
        index_repository(session, repository, demo_repo, app_settings)
        session.commit()
        retriever = CodeRetriever(
            session, settings=app_settings, embedding_provider=build_embedding_provider(app_settings)
        )
        bundle = retriever.retrieve(
            repository=repository, queries=["add two numbers", "test_add"], strategy=RetrievalStrategy.HYBRID
        )
        assert bundle.related_tests, (
            "the fixture repository mirrors tests/test_calculator.py for calculator.py"
        )
        assert all(item.is_test for item in bundle.related_tests)
        assert all(not item.is_test for item in bundle.context)
        assert bundle.findings_context_count == len(bundle.context) + len(bundle.related_tests)

    def test_strategy_names_are_stable_for_the_evaluation_harness(self) -> None:
        assert [strategy.value for strategy in RetrievalStrategy] == [
            "static",
            "bm25",
            "semantic",
            "hybrid",
            "hybrid_rerank",
        ]


class TestCorpusLookup:
    def test_corpus_indexes_documents_by_id_and_key(self) -> None:
        doc = document("s1", "parse_diff")
        corpus = Corpus(
            version_id="ver_test",
            repository_id="repo_test",
            tree_hash="tree",
            documents=[doc],
            vectors=[],
            embedding_provider="hashing",
            embedding_model="hashing-ngram-v2",
            embedding_dim=64,
            semantic_is_neural=False,
        )
        assert corpus.by_id()["s1"] is doc
        assert doc.key() == ("pkg/x.py", "parse_diff")
        assert corpus.size == 1
        assert corpus.tests() == []
        assert corpus.semantic_is_neural is False
        assert corpus.metadata()["documents"] == 1
