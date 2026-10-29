"""
Semantic video search API endpoints
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, status, Depends
from src.schemas.search import VideoSearchRequest, VideoSearchResponse
from src.services.auth_service import CurrentUser
from src.config import get_settings
from src.services.embedding_service import get_embedding_service
from src.services.vastdb_service import get_vastdb_service
from src.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["Search"])


@router.post("", response_model=VideoSearchResponse)
async def search_videos(
    request: VideoSearchRequest,
    current_user: CurrentUser
):
    """
    Hybrid semantic search (caption text + video embeddings), with VastDB ACL and metadata filters.
    """
    logger.info(
        f"Search request from {current_user.username}: query='{request.query}', "
        f"top_k={request.top_k}, llm_top_n={request.llm_top_n}, "
        f"min_similarity={request.min_similarity}, metadata_filters={request.metadata_filters or {}}"
    )

    try:
        embedding_service = get_embedding_service()
        settings = get_settings()
        hybrid_text_weight = (
            request.hybrid_text_weight
            if request.hybrid_text_weight is not None
            else settings.hybrid_text_weight
        )

        logger.info(f"[EMBEDDING] Hybrid query embeddings for: '{request.query}'")
        query_embedding, text_ms = embedding_service.generate_embedding(
            request.query, input_type="query"
        )
        query_visual_embedding, visual_ms = embedding_service.generate_visual_embedding(
            request.query, input_type="query"
        )
        embedding_time_ms = text_ms + visual_ms

        vastdb_service = get_vastdb_service()
        logger.info(
            f"[SEARCH] Hybrid search on VastDB | include_public={request.include_public} | "
            f"public_only={request.public_only} | time_filter={request.time_filter}"
        )

        segment_fetch_k = min(max(request.top_k * 5, request.top_k), 100)

        results, search_time_ms, permission_filtered, formatted_sql = vastdb_service.similarity_search(
            query_embedding=query_embedding,
            top_k=segment_fetch_k,
            user=current_user,
            tags=request.tags if request.tags else None,
            include_public=request.include_public,
            public_only=request.public_only,
            time_filter=request.time_filter,
            custom_start_date=request.custom_start_date,
            custom_end_date=request.custom_end_date,
            metadata_filters=request.metadata_filters,
            min_similarity=request.min_similarity,
            user_query_text=request.query,
            search_mode="hybrid",
            query_visual_embedding=query_visual_embedding,
            hybrid_text_weight=hybrid_text_weight,
        )

        chunk_results = vastdb_service.group_results_by_chunk(
            results, current_user, request.query, request.top_k
        )
        chunk_results = await asyncio.to_thread(
            vastdb_service.attach_stream_siblings,
            chunk_results,
            current_user,
            request.include_public,
            request.public_only,
        )

        logger.info(
            f"[SEARCH] Found {len(results)} segment hits → {len(chunk_results)} chunks "
            f"in {search_time_ms:.2f}ms"
        )
        logger.info(f"[SEARCH] Permission filtered: {permission_filtered} videos")

        if results:
            top_scores = [f"{r.similarity_score:.4f}" for r in results[:3]]
            logger.debug(f"[SEARCH] Top scores: {', '.join(top_scores)}")

        llm_synthesis = None
        if len(chunk_results) > 0:
            llm_results = chunk_results[:request.llm_top_n]
            logger.info(f"[LLM] Generating AI synthesis for top {len(llm_results)} chunks")
            try:
                llm_service = get_llm_service()
                results_dict = [
                    llm_service.chunk_search_result_to_evidence(c)
                    for c in llm_results
                ]
                llm_synthesis = llm_service.synthesize_search_results(
                    query=request.query,
                    top_results=results_dict,
                    custom_system_prompt=request.system_prompt,
                )
            except Exception as e:
                logger.error(f"[LLM] Failed to generate synthesis: {e}")
                llm_synthesis = {
                    "response": f"Failed to generate AI synthesis: {str(e)}",
                    "segments_used": 0,
                    "model": "",
                    "tokens_used": 0,
                    "processing_time": 0.0,
                    "error": str(e),
                }

        return VideoSearchResponse(
            results=results[:request.top_k],
            chunk_results=chunk_results,
            total=len(results[:request.top_k]),
            chunk_total=len(chunk_results),
            query=request.query,
            embedding_time_ms=embedding_time_ms,
            search_time_ms=search_time_ms,
            permission_filtered=permission_filtered,
            llm_synthesis=llm_synthesis,
            sql_query=formatted_sql,
        )

    except Exception as e:
        logger.error(f"Search failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}",
        )
