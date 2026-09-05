"""Actor workbench: reference → master + multipanel three-view (no body/hair split)."""

from app.pipelines.actor import workflow as w


def test_ref_master_uses_full_identity():
    assert "REFERENCE photo" in w.REF_ACTOR_MASTER_PROMPT
    assert "Preserve the same person" in w.REF_ACTOR_MASTER_PROMPT
    assert "USER DESCRIPTION" in w.REF_ACTOR_MASTER_PROMPT or "written description" in w.REF_ACTOR_MASTER_PROMPT
    assert w.REF_FACE_ONLY_MASTER_PROMPT == w.REF_ACTOR_MASTER_PROMPT


def test_build_appends_description_into_ref_master():
    graph, _ = w.build_actor_prompt(
        description="bare feet, no shoes",
        actor_image_name="face.png",
    )
    ref_prompt = graph["63"]["inputs"]["value"]
    assert "USER DESCRIPTION:" in ref_prompt
    assert "bare feet, no shoes" in ref_prompt
    # must not hard-force shoes over user text
    assert "Wear simple closed shoes" not in ref_prompt


def test_fullbody_prompt_matches_0081_baseline():
    # Same multipanel language as working job 008126d9
    assert "Image 1 is the actor MASTER (full-body front)" in w.FULLBODY_THREEVIEW_PROMPT
    assert "original actor REFERENCE photo" in w.FULLBODY_THREEVIEW_PROMPT
    assert "exactly THREE equal vertical panels" in w.FULLBODY_THREEVIEW_PROMPT
    assert "LEFT: exact front view" in w.FULLBODY_THREEVIEW_PROMPT
    assert "RIGHT: exact back view" in w.FULLBODY_THREEVIEW_PROMPT
    assert "side view" not in w.DEFAULT_NEGATIVE


def test_build_feeds_ref_into_master_and_threeview():
    graph, seed = w.build_actor_prompt(
        description="test actor with long hair",
        body_description="IGNORED tall",
        hair_description="IGNORED bun",
        actor_image_name="face.png",
    )
    # body/hair tracks cleared
    assert graph["59"]["inputs"]["value"] == ""
    assert graph["60"]["inputs"]["value"] == ""
    assert "test actor" in graph["58"]["inputs"]["value"]
    # master ref path uses actor image
    assert graph["15"]["inputs"]["image"] == "face.png"
    assert graph["16"]["inputs"]["image1"] == [w.NODE_ACTOR_IMAGE, 0]
    assert graph["63"]["inputs"]["value"].startswith(w.REF_ACTOR_MASTER_PROMPT)
    assert "USER DESCRIPTION:" in graph["63"]["inputs"]["value"]
    assert "test actor with long hair" in graph["63"]["inputs"]["value"]
    # three-view multipanel: master + actor ref
    assert graph["40"]["inputs"]["image1"] == ["30", 0]
    assert graph["40"]["inputs"]["image2"] == [w.NODE_ACTOR_IMAGE, 0]
    assert graph["46"]["inputs"]["images"] == ["45", 0]
    assert graph["32"]["inputs"]["image"] == ["45", 0]
    assert "200" not in graph
    assert seed is not None


def test_build_feeds_original_actor_ref_into_wardrobe_identity_lock():
    graph, _ = w.build_actor_prompt(
        description="test actor",
        actor_image_name="face.png",
        wardrobe_image_name="coat.png",
    )

    wardrobe = graph["24"]["inputs"]
    assert wardrobe["image1"] == ["22", 0]
    assert wardrobe["image2"] == ["56", 0]
    assert wardrobe["image3"] == [w.NODE_ACTOR_IMAGE, 0]
    assert "Image 3 is the original actor reference" in wardrobe["prompt"]
    assert "identity only" in wardrobe["prompt"]


def test_wardrobe_headwear_and_footwear_are_opt_in():
    graph, _ = w.build_actor_prompt(
        description="test actor",
        actor_image_name="face.png",
        wardrobe_image_name="coat.png",
    )

    extraction = graph[w.NODE_WARDROBE_EXTRACT_PROMPT]["inputs"]["prompt"]
    transfer = graph["24"]["inputs"]["prompt"]
    assert "Exclude headwear" in extraction
    assert "Exclude shoes and other footwear" in extraction
    assert "do not add headwear" in transfer
    assert "if the master is barefoot, keep it barefoot" in transfer
    threeview = graph[w.NODE_FULLBODY_THREEVIEW_PROMPT]["inputs"]["value"]
    assert "No hat or headwear in any panel" in threeview
    assert "headwear (if present)" not in threeview


def test_build_can_extract_and_apply_headwear_and_footwear():
    graph, _ = w.build_actor_prompt(
        description="test actor",
        actor_image_name="face.png",
        wardrobe_image_name="pirate.png",
        include_headwear=True,
        include_footwear=True,
    )

    extraction = graph[w.NODE_WARDROBE_EXTRACT_PROMPT]["inputs"]["prompt"]
    transfer = graph["24"]["inputs"]["prompt"]
    assert "Include clearly visible hat or headwear" in extraction
    assert "Include clearly visible shoes or boots" in extraction
    assert "Apply the hat or headwear from image 2" in transfer
    assert "Apply the exact footwear from image 2" in transfer
    assert "if the master is barefoot, keep it barefoot" not in transfer
    threeview = graph[w.NODE_FULLBODY_THREEVIEW_PROMPT]["inputs"]["value"]
    assert "The same hat or headwear visible on the master must appear in all three panels" in threeview
    assert "No hat or headwear in any panel" not in threeview


def test_derive_mode_reference():
    assert w.derive_mode(has_actor_ref=True, has_wardrobe_ref=False) == "reference"
    assert w.derive_mode(has_actor_ref=False, has_wardrobe_ref=False) == "text"
