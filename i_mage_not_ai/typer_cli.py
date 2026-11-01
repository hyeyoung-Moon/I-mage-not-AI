"""Implementation of the i-mage-not-ai command line interface."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
from typing import Optional
import typer
from .core import RULES, RULE_PACKS, inspect_image, load_manifest, review as run_review, verify_images, resolve_rules, plan_review
from .policies import load_project_rules, match_project_rules
from .game import load_trace, load_policy, analyze_trace, load_runtime_evidence, load_runtime_policy, analyze_runtime_evidence, aggregate_game_gate
from .render_evidence import analyze_render_evidence, load_render_evidence
from .benchmark import score_files, generate_synthetic_dataset, create_submission_template, verify_synthetic_submission, update_leaderboard, render_gallery
from .project_discovery import discover_projects, create_project_manifest, install_unity_exporter
from .engine_export import run_engine_export
from .engine_capture import run_engine_capture, run_engine_capture_suite, install_unity_capture_adapter
from .measurements import compare_feature_context, measure_manifest
from .openpose_anatomy import analyze_openpose_anatomy
from .learned_similarity import compare_dinov2, compare_lpips

app=typer.Typer(no_args_is_help=True,help="Deterministic image design constraint linter")
manifest_app=typer.Typer(no_args_is_help=True)
delegate_app=typer.Typer(no_args_is_help=True)
game_app=typer.Typer(no_args_is_help=True,help="Capture rendered engine frames and analyze game/runtime evidence")
benchmark_app=typer.Typer(no_args_is_help=True,help="Score labeled benchmark findings")
project_app=typer.Typer(no_args_is_help=True,help="Discover local Godot/Unity project assets without executing project code")
character_app=typer.Typer(no_args_is_help=True,help="Check saved OpenPose keypoints against explicit anatomy policies")
app.add_typer(manifest_app,name="manifest")
app.add_typer(delegate_app,name="delegate")
app.add_typer(game_app,name="game")
app.add_typer(benchmark_app,name="benchmark")
app.add_typer(project_app,name="project")
app.add_typer(character_app,name="character")

def emit(data, output: Optional[Path]=None):
    rendered=json.dumps(data,indent=2,ensure_ascii=False)
    if output: output.write_text(rendered+"\n",encoding="utf-8")
    else: typer.echo(rendered)

def fail(exc: Exception):
    typer.echo(json.dumps({"status":"INVALID_INPUT","error":str(exc)},ensure_ascii=False),err=True)
    raise typer.Exit(3)

def internal_error(exc: Exception):
    typer.echo(json.dumps({"status":"INTERNAL_ERROR","error":str(exc)},ensure_ascii=False),err=True)
    raise typer.Exit(4)

def review_exit_code(result: dict) -> int:
    if result.get("status") == "FAIL": return 1
    if result.get("status") == "INCONCLUSIVE" or result.get("completeness") != "COMPLETE": return 2
    return 0

def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def build_review_plan(manifest_path: Path, manifest: dict, rules_file: Optional[Path], policies: list) -> dict:
    execution_plan = plan_review(manifest, policies)
    return {
        **execution_plan,
        "schema_version": "i-mage-not-ai/review-plan/1",
        "bindings": {
            "manifest_sha256": file_sha256(manifest_path),
            "rules_file_sha256": file_sha256(rules_file) if rules_file else None,
        },
        "status": execution_plan["status"],
        "execution_plan": execution_plan,
        "review_intent": [],
    }

def validate_review_plan(plan_path: Path, manifest_path: Path, manifest: dict, rules_file: Optional[Path], policies: list) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    expected = build_review_plan(manifest_path, manifest, rules_file, policies)
    if not isinstance(plan, dict) or plan.get("schema_version") != expected["schema_version"]:
        raise ValueError("unsupported review plan schema")
    if plan.get("bindings") != expected["bindings"]:
        raise ValueError("review plan is stale: manifest or rules file changed")
    if plan.get("execution_plan") != expected["execution_plan"] or plan.get("status") != expected["status"]:
        raise ValueError("review plan execution section does not match the current deterministic CLI plan")
    if not isinstance(plan.get("review_intent"), list):
        raise ValueError("review_intent must be a list")
    expected_rule_ids = [check["rule_id"] for check in expected["execution_plan"]["checks"]]
    intents = plan["review_intent"]
    if any(not isinstance(item, dict) for item in intents):
        raise ValueError("each review_intent entry must be an object")
    intent_rule_ids = [item.get("rule_id") for item in intents]
    if sorted(intent_rule_ids) != sorted(expected_rule_ids):
        raise ValueError("review_intent must contain exactly one rationale for every planned rule")
    if any(not isinstance(item.get("purpose"), str) or not item["purpose"].strip()
           or not isinstance(item.get("threshold_source"), str) or not item["threshold_source"].strip()
           for item in intents):
        raise ValueError("each review_intent needs a non-empty purpose and threshold_source")
    return plan

@benchmark_app.command("score")
def benchmark_score(ground_truth: Path=typer.Option(...,"--ground-truth"),predictions: Path=typer.Option(...,"--predictions"),output: Optional[Path]=typer.Option(None,"--output")):
    """Compute rule-level precision, recall and F1 for one dataset version."""
    try: emit(score_files(ground_truth,predictions),output)
    except (ValueError,OSError,json.JSONDecodeError) as exc: fail(exc)

@benchmark_app.command("generate-synthetic")
def benchmark_generate_synthetic(output_dir: Path=typer.Option(...,"--output-dir"),count: int=typer.Option(20,"--count",min=1,max=1000)):
    """Generate deterministic synthetic flat-icon cases with injected palette faults."""
    try: emit(generate_synthetic_dataset(output_dir,count))
    except (ValueError,OSError) as exc: fail(exc)

@benchmark_app.command("evaluate")
def benchmark_evaluate(dataset: Path=typer.Option(...,"--dataset"),submission: Path=typer.Option(...,"--submission"),output: Optional[Path]=typer.Option(None,"--output")):
    """Independently measure and score a three-track synthetic comparison."""
    try:
        emit(verify_synthetic_submission(dataset,submission),output)
    except (ValueError,OSError,json.JSONDecodeError) as exc: fail(exc)

@benchmark_app.command("init-submission")
def benchmark_init_submission(dataset: Path=typer.Option(...,"--dataset"),output: Path=typer.Option(...,"--output")):
    """Create a three-track submission skeleton for a benchmark dataset."""
    try:
        submission=create_submission_template(dataset,output)
        emit({"status":"CREATED","path":str(output),"case_count":len(submission["tracks"]["agent_only"]["assets"])})
    except (ValueError,OSError,json.JSONDecodeError) as exc: fail(exc)

@benchmark_app.command("leaderboard-add")
def benchmark_leaderboard_add(result: Path=typer.Option(...,"--result"),leaderboard: Path=typer.Option(...,"--leaderboard"),run_id: str=typer.Option(...,"--run-id")):
    """Add a verified synthetic evaluation to its version-specific local board."""
    try: emit(update_leaderboard(leaderboard,json.loads(result.read_text(encoding="utf-8")),run_id))
    except (ValueError,OSError,json.JSONDecodeError) as exc: fail(exc)

@benchmark_app.command("gallery")
def benchmark_gallery(dataset: Path=typer.Option(...,"--dataset"),submission: Path=typer.Option(...,"--submission"),result: Path=typer.Option(...,"--result"),output: Path=typer.Option(...,"--output")):
    """Create a static, local before/after report for a verified synthetic run."""
    try: emit({"status":"CREATED","path":str(render_gallery(dataset,submission,result,output))})
    except (ValueError,OSError,json.JSONDecodeError) as exc: fail(exc)

def markdown_report(report):
    lines=[f"# Image Review: {report.get('status','UNKNOWN')}","",f"Completeness: **{report.get('completeness','UNKNOWN')}**","",
           f"Coverage: {report.get('coverage',{}).get('completed',0)}/{report.get('coverage',{}).get('required',0)} required checks completed","",
           "| Rule | Feature | Status | Measured | Threshold |","|---|---|---|---:|---:|"]
    for row in report.get("results",[]):
        lines.append(f"| {row.get('rule_id')} | {row.get('feature_id') or 'asset'} | {row.get('status')} | `{json.dumps(row.get('measured'),ensure_ascii=False)}` | `{json.dumps(row.get('threshold'),ensure_ascii=False)}` |")
    return "\n".join(lines)+"\n"

@app.command("inspect")
def inspect_asset(asset: Path):
    try: emit(inspect_image(asset))
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@app.command("measure")
def measure_asset(manifest: Path=typer.Option(...,"--manifest"),output: Optional[Path]=typer.Option(None,"--output")):
    """Emit deterministic raster and approved-mask measurements without quality thresholds."""
    try:
        emit(measure_manifest(manifest),output)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@app.command("compare")
def compare_feature(manifest: Path=typer.Option(...,"--manifest"),feature: str=typer.Option(...,"--feature"),
                    boundary_radius: int=typer.Option(8,"--boundary-radius",min=1),output: Optional[Path]=typer.Option(None,"--output")):
    """Compare a foreground feature's color, boundary, texture, and lighting diagnostics with its context."""
    try:
        emit(compare_feature_context(manifest,feature,boundary_radius),output)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@character_app.command("anatomy")
def character_anatomy(image: Path=typer.Option(...,"--image"),
                      openpose_json: Path=typer.Option(...,"--openpose-json"),
                      policy: Path=typer.Option(...,"--policy"),
                      output: Optional[Path]=typer.Option(None,"--output")):
    """Measure BODY_25 proportions and joint angles using explicit limits."""
    try:
        report=analyze_openpose_anatomy(image,openpose_json,policy)
        emit(report,output)
        if report["status"] == "FAIL": raise typer.Exit(1)
        if report["status"] != "PASS" or report["completeness"] != "COMPLETE": raise typer.Exit(2)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@app.command("similarity")
def learned_similarity(reference: Path=typer.Option(...,"--reference"),
                       candidate: Path=typer.Option(...,"--candidate"),
                       metric: str=typer.Option(...,"--metric",help="lpips or dinov2"),
                       max_distance: Optional[float]=typer.Option(None,"--max-distance"),
                       min_similarity: Optional[float]=typer.Option(None,"--min-similarity"),
                       network: str=typer.Option("alex","--network"),
                       weight_cache: Optional[Path]=typer.Option(None,"--weight-cache"),
                       model_dir: Optional[Path]=typer.Option(None,"--model-dir"),
                       output: Optional[Path]=typer.Option(None,"--output")):
    """Compare aligned images using local LPIPS weights or a local DINOv2 model."""
    try:
        if metric == "lpips":
            if min_similarity is not None or model_dir is not None:
                raise ValueError("LPIPS accepts --max-distance and optional --weight-cache only")
            report=compare_lpips(reference,candidate,network=network,
                                 max_distance=max_distance,weight_cache=weight_cache)
        elif metric == "dinov2":
            if max_distance is not None or weight_cache is not None or network != "alex":
                raise ValueError("DINOv2 accepts --min-similarity and --model-dir only")
            if model_dir is None:
                raise ValueError("--model-dir is required for DINOv2; remote model downloads are disabled")
            report=compare_dinov2(reference,candidate,model_dir=model_dir,
                                  min_similarity=min_similarity)
        else:
            raise ValueError("--metric must be lpips or dinov2")
        emit(report,output)
        if report["status"] == "FAIL": raise typer.Exit(1)
        if report["status"] != "PASS" or report["completeness"] != "COMPLETE": raise typer.Exit(2)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@manifest_app.command("validate")
def validate_manifest(manifest: Path):
    try:
        data,_=load_manifest(manifest); emit({"status":"VALID","schema_version":data["schema_version"]})
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@delegate_app.command("preview")
def preview(manifest: Optional[Path]=typer.Option(None,"--manifest"), path: Path=typer.Option(Path("."),"--path")):
    try:
        if manifest:
            data,_=load_manifest(manifest)
            emit({"asset":{"path":data["asset"]["path"],"exists":True},"intent":data.get("intent",{}),"features":[{"id":f["id"],"role":f["role"],"parent_id":f.get("parent_id")} for f in data.get("features",[])],"planned_checks":len(resolve_rules(data))})
        else:
            files=sorted(str(p) for p in path.rglob("*") if p.is_file() and p.suffix.lower() in {".png",".jpg",".jpeg",".webp",".svg"})
            emit({"images":files})
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@delegate_app.command("rules")
def rules(manifest: Path=typer.Option(...,"--manifest"),rules_file: Optional[Path]=typer.Option(None,"--rules-file")):
    try:
        data,_=load_manifest(manifest); selected=resolve_rules(data); registry={r["algorithm"]:r for r in RULES}
        result={"manifest_schema":data["schema_version"],"resolved_rules":[{**x,**registry.get(x["algorithm"],{})} for x in selected]}
        if rules_file:
            matched=match_project_rules(data,load_project_rules(rules_file))
            result["project_rules"]=[{"id":r.id,"version":r.version,"priority":r.priority,"feature_id":f,"algorithm":r.algorithm.model_dump(),"assertion":r.assertion.model_dump()} for r,f in matched]
        emit(result)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@delegate_app.command("packs")
def packs(kind: Optional[str]=typer.Option(None,"--kind")):
    if kind and kind not in RULE_PACKS: raise typer.BadParameter(f"unknown pack {kind}; choose from {', '.join(RULE_PACKS)}")
    emit({key:value for key,value in RULE_PACKS.items() if kind is None or key==kind})

@app.command("plan")
def plan(manifest: Path=typer.Option(...,"--manifest"),rules_file: Optional[Path]=typer.Option(None,"--rules-file"),output: Optional[Path]=typer.Option(None,"--output")):
    try:
        data,_=load_manifest(manifest); policies=load_project_rules(rules_file) if rules_file else []
        result=build_review_plan(manifest,data,rules_file,policies)
        if rules_file:
            matched=match_project_rules(data,policies)
            result["project_rules"]=[{"id":r.id,"feature_id":f,"algorithm":r.algorithm.id,"status":"PLANNED"} for r,f in matched]
        emit(result,output)
        if result["status"]!="READY": raise typer.Exit(2)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@app.command("review")
def review(manifest: Path=typer.Option(...,"--manifest"),output: Optional[Path]=typer.Option(None,"--output"),output_format: str=typer.Option("json","--format"),rules_file: Optional[Path]=typer.Option(None,"--rules-file"),plan_file: Optional[Path]=typer.Option(None,"--plan")):
    if output_format not in {"json","markdown"}: raise typer.BadParameter("--format must be json or markdown")
    try:
        data,root=load_manifest(manifest); policies=load_project_rules(rules_file) if rules_file else []
        if plan_file:
            validate_review_plan(plan_file,manifest,data,rules_file,policies)
        result=run_review(data,root,policies)
        rendered=markdown_report(result) if output_format=="markdown" else json.dumps(result,indent=2,ensure_ascii=False)+"\n"
        if output: output.write_text(rendered,encoding="utf-8")
        else: typer.echo(rendered.rstrip("\n"))
        exit_code=review_exit_code(result)
        if exit_code: raise typer.Exit(exit_code)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@app.command("coverage")
def coverage(report: Path=typer.Option(...,"--report")):
    try:
        data=json.loads(report.read_text(encoding="utf-8")); cov=data.get("coverage")
        if not isinstance(cov,dict): raise ValueError("report has no coverage data")
        emit({"completeness":data.get("completeness"),**cov})
        if data.get("completeness")!="COMPLETE": raise typer.Exit(2)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@app.command("explain")
def explain(report: Path):
    try:
        data=json.loads(report.read_text(encoding="utf-8")); typer.echo(f"Overall: {data.get('status','UNKNOWN')}")
        for row in data.get("results",[]): typer.echo(f"{row.get('status')} {row.get('rule_id')} feature={row.get('feature_id')} measured={row.get('measured')} threshold={row.get('threshold')} region={row.get('region')} formula={row.get('formula')}")
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@app.command("verify")
def verify(before: Path=typer.Option(...,"--before"),after: Path=typer.Option(...,"--after"),report: Optional[Path]=typer.Option(None,"--report"),region: Optional[tuple[int,int,int,int]]=typer.Option(None,"--region")):
    try:
        allowed_mask=None
        if report:
            data=json.loads(report.read_text(encoding="utf-8"))
            if region is None and data.get("allowed_region"): region=tuple(data["allowed_region"])
            if data.get("allowed_mask"): allowed_mask=Path(data["allowed_mask"])
        result=verify_images(before,after,region,allowed_mask); emit(result)
        if result["status"]!="PASS": raise typer.Exit(1)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@app.command("diff")
def diff(before: Path=typer.Option(...,"--before"),after: Path=typer.Option(...,"--after")):
    try:
        result=verify_images(before,after); emit(result)
        if result["status"]!="PASS": raise typer.Exit(1)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@game_app.command("analyze")
def analyze_game_trace(trace: Path=typer.Option(...,"--trace"),policy: Optional[Path]=typer.Option(None,"--policy"),output: Optional[Path]=typer.Option(None,"--output")):
    try:
        game_trace=load_trace(trace); constraints=load_policy(policy)
        result=analyze_trace(game_trace,constraints,trace.resolve().parent); emit(result,output)
        exit_code=review_exit_code(result)
        if exit_code: raise typer.Exit(exit_code)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@game_app.command("runtime-check")
def check_runtime_evidence(evidence: Path=typer.Option(...,"--evidence"),policy: Path=typer.Option(...,"--policy"),output: Optional[Path]=typer.Option(None,"--output")):
    """Check observed runtime callback and particle samples using explicit numeric limits."""
    try:
        report=analyze_runtime_evidence(load_runtime_evidence(evidence),load_runtime_policy(policy))
        emit(report,output)
        exit_code=review_exit_code(report)
        if exit_code: raise typer.Exit(exit_code)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@game_app.command("render-check")
def check_render_evidence(evidence: Path=typer.Option(...,"--evidence"),policy: Path=typer.Option(...,"--policy"),output: Optional[Path]=typer.Option(None,"--output")):
    """Check engine capture provenance, frame changes, and event timing."""
    try:
        bundle,_=load_render_evidence(evidence)
        constraints=json.loads(policy.read_text(encoding="utf-8"))
        report=analyze_render_evidence(bundle,constraints)
        emit(report,output)
        exit_code=review_exit_code(report)
        if exit_code: raise typer.Exit(exit_code)
    except (ValueError,OSError,json.JSONDecodeError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@game_app.command("capture")
def capture_game(engine: str=typer.Option(...,"--engine"),project: Path=typer.Option(...,"--project"),
                 engine_executable: Path=typer.Option(...,"--engine-executable"),scene: str=typer.Option(...,"--scene"),
                 scenario_id: str=typer.Option(...,"--scenario-id"),output: Path=typer.Option(...,"--output"),
                 width: int=typer.Option(...,"--width",min=1,max=8192),height: int=typer.Option(...,"--height",min=1,max=8192),
                 frames: int=typer.Option(60,"--frames",min=2,max=10000),fps: int=typer.Option(60,"--fps",min=1,max=240),
                 event_map: Optional[Path]=typer.Option(None,"--event-map"),allow_project_code: bool=typer.Option(False,"--allow-project-code"),
                 camera_node_path: Optional[str]=typer.Option(None,"--camera-node-path"),
                 camera_distance_offset: float=typer.Option(0.0,"--camera-distance-offset"),
                 camera_zoom_scale: float=typer.Option(1.0,"--camera-zoom-scale"),
                 timeout: int=typer.Option(300,"--timeout",min=1,max=1800)):
    """Run a selected project scene with a renderer and save actual frame captures."""
    try:
        result=run_engine_capture(engine=engine,project_root=project,engine_executable=engine_executable,
            selection=scene,scenario_id=scenario_id,output=output,width=width,height=height,
            frame_count=frames,fps=fps,allow_project_code=allow_project_code,event_map=event_map,
            camera_node_path=camera_node_path,camera_distance_offset=camera_distance_offset,
            camera_zoom_scale=camera_zoom_scale,timeout_seconds=timeout)
        emit(result)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@game_app.command("capture-suite")
def capture_game_suite(engine: str=typer.Option(...,"--engine"),project: Path=typer.Option(...,"--project"),
                       engine_executable: Path=typer.Option(...,"--engine-executable"),scene: str=typer.Option(...,"--scene"),
                       scenario_id: str=typer.Option(...,"--scenario-id"),suite: Path=typer.Option(...,"--suite"),
                       render_policy: Path=typer.Option(...,"--render-policy"),review_report: Path=typer.Option(...,"--review-report"),
                       runtime_report: Path=typer.Option(...,"--runtime-report"),output: Path=typer.Option(...,"--output"),
                       frames: int=typer.Option(60,"--frames",min=2,max=10000),fps: int=typer.Option(60,"--fps",min=1,max=240),
                       event_map: Optional[Path]=typer.Option(None,"--event-map"),allow_project_code: bool=typer.Option(False,"--allow-project-code"),
                       timeout: int=typer.Option(300,"--timeout",min=1,max=1800)):
    """Capture every resolution/camera case, then automatically run render-check and game gate."""
    try:
        result=run_engine_capture_suite(engine=engine,project_root=project,engine_executable=engine_executable,
            selection=scene,scenario_id=scenario_id,suite_file=suite,event_map=event_map,
            render_policy_file=render_policy,review_report_file=review_report,runtime_report_file=runtime_report,
            output=output,frame_count=frames,fps=fps,allow_project_code=allow_project_code,timeout_seconds=timeout)
        emit(result)
        if result["status"]!="PASS": raise typer.Exit(1 if result["status"]=="FAIL" else 2)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@game_app.command("gate")
def game_gate(review_report: Path=typer.Option(...,"--review-report"),runtime_report: Path=typer.Option(...,"--runtime-report"),render_report: Path=typer.Option(...,"--render-report"),output: Optional[Path]=typer.Option(None,"--output")):
    """Aggregate required game trace, live runtime, and rendered-frame gates."""
    try:
        reports=[json.loads(path.read_text(encoding="utf-8")) for path in (review_report,runtime_report,render_report)]
        result=aggregate_game_gate(*reports)
        emit(result,output)
        exit_code=review_exit_code(result)
        if exit_code: raise typer.Exit(exit_code)
    except (ValueError,OSError,json.JSONDecodeError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@project_app.command("preview")
def preview_project(path: Path=typer.Option(...,"--path"),max_files: int=typer.Option(20000,"--max-files",min=1),max_depth: int=typer.Option(16,"--max-depth",min=0)):
    """List detected Godot/Unity projects, image files, references, and skipped paths."""
    try:
        result=discover_projects(path,max_files=max_files,max_depth=max_depth)
        emit(result)
        if result["status"]!="READY": raise typer.Exit(2)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@project_app.command("manifest")
def project_manifest(project: Path=typer.Option(...,"--project"),asset: str=typer.Option(...,"--asset"),output: Path=typer.Option(...,"--output")):
    """Write a hash-pinned, policy-free manifest draft for a selected project image."""
    try:
        project_root=project.expanduser().resolve(strict=True)
        output_path=output.expanduser().resolve()
        if output_path.parent!=project_root:
            raise ValueError("manifest output must be directly inside the selected project root so its asset path remains contained")
        data=create_project_manifest(project_root,asset)
        output_path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        emit({"status":"DRAFT_CREATED","manifest":str(output_path),"asset":data["asset"],
              "features":0,"constraints":0,"note":"Add semantic features and explicit project thresholds before treating this draft as a quality review."})
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@project_app.command("install-exporter")
def install_project_exporter(engine: str=typer.Option(...,"--engine"),project: Path=typer.Option(...,"--project")):
    """Install the Unity editor exporter source without running the project."""
    try:
        if engine != "unity": raise ValueError("only the Unity exporter requires installation; Godot uses the bundled CLI script")
        destination=install_unity_exporter(project)
        emit({"status":"INSTALLED","engine":engine,"path":str(destination),"executed_project_code":False,
              "note":"Unity may compile this editor extension the next time its editor opens."})
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@project_app.command("install-capture-adapter")
def install_capture_adapter(engine: str=typer.Option(...,"--engine"),project: Path=typer.Option(...,"--project")):
    """Install the Unity runtime capture hook and editor launcher without running the project."""
    try:
        if engine == "godot":
            emit({"status":"READY","engine":"godot","installed":False,
                  "runner":str(Path(__file__).resolve().parents[1]/"engine"/"godot"/"i_mage_not_ai_render_capture.gd"),
                  "note":"Godot capture uses the bundled external runner and does not modify the project."})
        elif engine == "unity":
            paths=install_unity_capture_adapter(project)
            emit({"status":"INSTALLED","engine":"unity","paths":paths,"executed_project_code":False,
                  "note":"Unity will compile the runtime hook/editor launcher the next time the Editor opens."})
        else:
            raise ValueError("engine must be godot or unity")
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)

@project_app.command("export-trace")
def export_project_trace(engine: str=typer.Option(...,"--engine"),project: Path=typer.Option(...,"--project"),
                         engine_executable: Path=typer.Option(...,"--engine-executable"),selection: str=typer.Option(...,"--selection"),
                         output: Path=typer.Option(...,"--output"),allow_project_code: bool=typer.Option(False,"--allow-project-code"),
                         timeout: int=typer.Option(180,"--timeout",min=1,max=1800),overwrite: bool=typer.Option(False,"--overwrite")):
    """Explicitly invoke an engine exporter. This can run scripts in the selected project."""
    try:
        result=run_engine_export(engine=engine,project_root=project,engine_executable=engine_executable,
            selection=selection,output=output,allow_project_code=allow_project_code,
            timeout_seconds=timeout,overwrite=overwrite)
        emit(result)
    except (ValueError,OSError) as exc: fail(exc)
    except typer.Exit: raise
    except Exception as exc: internal_error(exc)
