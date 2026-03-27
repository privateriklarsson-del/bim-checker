import streamlit as st
import ifcopenshell
import ifcopenshell.util.element
import ifctester
import ifctester.ids
import tempfile
import os
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from bcf.v2.bcfxml import BcfXml
from bcf.v2 import model as mdl

# --- Config ---
IDS_FOLDER = Path("ids_files")
APP_TITLE = "JM BIM Checker"

# --- TypeID to ClassCode mapping ---
TYPEID_CLASSCODE_MAP = {
    "IWS": "43.CB/41",
}


# --- Exceptions handling ---
def load_exceptions(uploaded_exc):
    """Load exceptions from CSV or Excel. Returns a set of (TypeID, Rule) tuples."""
    exceptions = {}
    if uploaded_exc is None:
        return exceptions
    try:
        if uploaded_exc.name.endswith(".csv"):
            df = pd.read_csv(uploaded_exc)
        else:
            df = pd.read_excel(uploaded_exc)
        # Normalize column names
        df.columns = [c.strip() for c in df.columns]
        for _, row in df.iterrows():
            type_id = str(row.get("TypeID", "")).strip()
            rule = str(row.get("Rule", "*")).strip()
            if type_id:
                key = (type_id, rule)
                exceptions[key] = {
                    "approved_by": str(row.get("ApprovedBy", "")),
                    "date": str(row.get("Date", "")),
                    "reference": str(row.get("Reference", "")),
                    "reason": str(row.get("Reason", "")),
                }
    except Exception as e:
        st.sidebar.error(f"Could not load exceptions: {e}")
    return exceptions


def is_excepted(entity, ifc_file, rule_name, exceptions):
    """Check if a failed entity is covered by an exception."""
    if not exceptions:
        return False, None
    # Get TypeID from JM pset
    psets = ifcopenshell.util.element.get_psets(entity)
    jm = psets.get("JM", {})
    type_id = jm.get("TypeID", "")
    if not type_id:
        return False, None
    # Check exact match (TypeID + Rule)
    key = (type_id, rule_name)
    if key in exceptions:
        return True, exceptions[key]
    # Check wildcard (TypeID + any rule)
    key_wild = (type_id, "*")
    if key_wild in exceptions:
        return True, exceptions[key_wild]
    return False, None


# --- Simple auth ---
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if st.session_state.authenticated:
        return True
    st.title(f"\U0001F512 {APP_TITLE}")
    st.markdown("Log in to access the BIM checker.")
    password = st.text_input("Password", type="password")
    if st.button("Log in"):
        if password == "jm2025":
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Wrong password.")
    return False


def load_ids_files():
    ids_files = {}
    if IDS_FOLDER.exists():
        for f in sorted(IDS_FOLDER.glob("*.ids")):
            try:
                ids_obj = ifctester.ids.open(str(f))
                ids_files[f.stem] = {"path": f, "ids": ids_obj}
            except Exception as e:
                st.warning(f"Could not load {f.name}: {e}")
    return ids_files


def add_bcf_viewpoint(topic, issue, ifc_file):
    """Add a viewpoint to a BCF topic, aimed at the first entity's placement.
    
    Uses add_viewpoint(element) for proper camera positioning,
    then extends the selection with remaining GUIDs.
    """
    first_entity = issue.get("first_entity")
    guids = issue.get("guids", [])
    
    if first_entity is not None and hasattr(first_entity, 'ObjectPlacement') and first_entity.ObjectPlacement:
        # Best case: use element placement for camera
        viewpoint = topic.add_viewpoint(first_entity)
        # Extend selection with all other GUIDs
        if len(guids) > 1:
            vi = viewpoint.visualization_info
            if vi.components and vi.components.selection:
                existing_guids = {c.ifc_guid for c in vi.components.selection.component}
                for guid in guids:
                    if guid not in existing_guids:
                        vi.components.selection.component.append(
                            mdl.Component(ifc_guid=guid)
                        )
    elif guids:
        # Fallback: try to find entity from GUID in ifc_file for camera
        fallback_entity = None
        if ifc_file is not None:
            try:
                fallback_entity = ifc_file.by_guid(guids[0])
            except Exception:
                pass
        
        if fallback_entity is not None and hasattr(fallback_entity, 'ObjectPlacement') and fallback_entity.ObjectPlacement:
            viewpoint = topic.add_viewpoint(fallback_entity)
            if len(guids) > 1:
                vi = viewpoint.visualization_info
                if vi.components and vi.components.selection:
                    existing_guids = {c.ifc_guid for c in vi.components.selection.component}
                    for guid in guids:
                        if guid not in existing_guids:
                            vi.components.selection.component.append(
                                mdl.Component(ifc_guid=guid)
                            )
        else:
            # Last resort: point-based, but use [0,0,5] to at least not be underground
            topic.add_viewpoint_from_point_and_guids(
                np.array([0.0, 0.0, 5.0]),
                *guids
            )


def main():
    if not check_password():
        return

    # --- Sidebar ---
    with st.sidebar:
        st.title(APP_TITLE)
        st.markdown("---")

        st.markdown("**Exceptions**")
        uploaded_exc = st.file_uploader(
            "Upload exceptions file",
            type=["csv", "xlsx"],
            help="CSV or Excel with columns: TypeID, Rule, ApprovedBy, Date, Reference, Reason"
        )
        exceptions = load_exceptions(uploaded_exc)
        if exceptions:
            st.success(f"{len(exceptions)} exceptions loaded")

        st.markdown("---")
        st.markdown("**How to use:**")
        st.markdown(
            "1. Upload your IFC file\n"
            "2. Optionally upload exceptions file\n"
            "3. Select rule sets\n"
            "4. Click **Run Validation**\n"
            "5. Review results"
        )
        st.markdown("---")
        st.markdown("*Prototype v0.2*")

    # --- Main area ---
    st.title("\U0001F3D7\uFE0F IFC Model Checker")
    st.markdown("Upload an IFC file and validate it against JM's BIM requirements.")

    ids_files = load_ids_files()
    if not ids_files:
        st.error("No IDS rule files found in the ids_files/ folder.")
        return

    uploaded_file = st.file_uploader("Upload IFC file", type=["ifc"])
    st.caption("📌 File naming convention: **D-PP-V-NN.ifc** — e.g. `A-40-V-02.ifc` (Discipline-Project-Version-Part)")

    st.subheader("Select rule sets")
    selected_ids = []
    cols = st.columns(2)
    for i, (name, data) in enumerate(ids_files.items()):
        col = cols[i % 2]
        with col:
            ids_obj = data["ids"]
            title = name.replace("_", " ")
            info_text = ""
            if hasattr(ids_obj, 'info') and ids_obj.info:
                if hasattr(ids_obj.info, 'description'):
                    info_text = ids_obj.info.description
            if st.checkbox(title, value=True, help=info_text):
                selected_ids.append((name, data))

    st.markdown("---")
    run_button = st.button("\U0001F680 Run Validation", type="primary", disabled=uploaded_file is None)

    if run_button and uploaded_file is not None:
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name

        try:
            with st.spinner("Parsing IFC file..."):
                ifc_file = ifcopenshell.open(tmp_path)

            st.success(
                f"Loaded **{uploaded_file.name}** \u2014 "
                f"Schema: {ifc_file.schema}, "
                f"Elements: {len(list(ifc_file))}"
            )

            all_results = []
            bcf_issues = []
            new_exceptions = []  # Collect potential new exceptions for export

            for name, data in selected_ids:
                ids_obj = ifctester.ids.open(str(data["path"]))

                with st.spinner(f"Checking: {name.replace('_', ' ')}..."):
                    ids_obj.validate(ifc_file)

                st.subheader(f"\U0001F4CB {name.replace('_', ' ')}")

                for spec in ids_obj.specifications:
                    applicable = spec.applicable_entities if spec.applicable_entities else []
                    total = len(applicable)
                    failed = spec.failed_entities if spec.failed_entities else set()

                    if spec.status is True:
                        st.markdown(f"\u2705 **{spec.name}** \u2014 {total} elements checked, all passed")
                    elif spec.status is False:
                        # Split failures into real failures and approved exceptions
                        real_failures = {}
                        excepted_items = {}

                        for req in spec.requirements:
                            if hasattr(req, 'failures') and req.failures:
                                for failure in req.failures:
                                    if isinstance(failure, dict):
                                        entity = failure.get("element") or failure.get("entity")
                                        reason = failure.get("reason", "Unknown")
                                    else:
                                        entity = getattr(failure, 'element', None) or getattr(failure, 'entity', None)
                                        reason = getattr(failure, 'reason', "Unknown")
                                    if entity is None:
                                        continue
                                    eid = entity.id()
                                    entity_name = entity.Name if hasattr(entity, 'Name') and entity.Name else "\u2014"
                                    is_exc, exc_info = is_excepted(entity, ifc_file, spec.name, exceptions)

                                    psets = ifcopenshell.util.element.get_psets(entity)
                                    type_id = psets.get("JM", {}).get("TypeID", "")

                                    item = {
                                        "type": entity.is_a(),
                                        "name": entity_name,
                                        "type_id": type_id,
                                        "reasons": [],
                                        "entity": entity,
                                    }

                                    if is_exc:
                                        if eid not in excepted_items:
                                            excepted_items[eid] = {**item, "exception": exc_info}
                                        excepted_items[eid]["reasons"].append(str(reason))
                                    else:
                                        if eid not in real_failures:
                                            real_failures[eid] = item
                                        real_failures[eid]["reasons"].append(str(reason))

                        real_count = len(real_failures)
                        exc_count = len(excepted_items)

                        # Display real failures
                        if real_count > 0:
                            with st.expander(f"\u274C **{spec.name}** \u2014 {real_count} failed{f', {exc_count} approved exceptions' if exc_count else ''}", expanded=False):
                                rows = []
                                for eid, info in sorted(real_failures.items()):
                                    rows.append({
                                        "ID": f"#{eid}",
                                        "Type": info["type"],
                                        "Name": info["name"],
                                        "TypeID": info["type_id"],
                                        "Reason": "; ".join(info["reasons"][:3]),
                                    })
                                    # Add to potential new exceptions
                                    if info["type_id"]:
                                        new_exceptions.append({
                                            "TypeID": info["type_id"],
                                            "Rule": spec.name,
                                            "ElementName": info["name"],
                                            "ApprovedBy": "",
                                            "Date": "",
                                            "Reference": "",
                                            "Reason": "",
                                        })
                                st.dataframe(rows, use_container_width=True, hide_index=True)

                                if total - real_count - exc_count > 0:
                                    st.markdown(f"*{total - real_count - exc_count} elements passed this check.*")
                        elif exc_count > 0:
                            st.markdown(f"\u2705 **{spec.name}** \u2014 {total} checked, all passed ({exc_count} via approved exceptions)")

                        # Display approved exceptions
                        if exc_count > 0:
                            with st.expander(f"\u26A0\uFE0F **Approved exceptions** for {spec.name} \u2014 {exc_count} items", expanded=False):
                                exc_rows = []
                                for eid, info in sorted(excepted_items.items()):
                                    exc = info["exception"]
                                    exc_rows.append({
                                        "ID": f"#{eid}",
                                        "TypeID": info["type_id"],
                                        "Name": info["name"],
                                        "Approved By": exc.get("approved_by", ""),
                                        "Reference": exc.get("reference", ""),
                                        "Reason": exc.get("reason", ""),
                                    })
                                st.dataframe(exc_rows, use_container_width=True, hide_index=True)

                        # BCF: only real failures, not exceptions
                        if real_count > 0:
                            guids = []
                            first_entity = None
                            for eid, info in real_failures.items():
                                guid = getattr(info["entity"], 'GlobalId', None)
                                if guid:
                                    guids.append(guid)
                                    if first_entity is None:
                                        first_entity = info["entity"]
                            if guids:
                                bcf_issues.append({
                                    "title": f"{name}: {spec.name}",
                                    "description": f"{real_count} elements failed (excl. {exc_count} exceptions). Rule set: {name}",
                                    "guids": guids,
                                    "first_entity": first_entity,
                                })

                        fail_status = "FAIL" if real_count > 0 else "PASS"
                    else:
                        st.markdown(f"\u26A0\uFE0F **{spec.name}** \u2014 No applicable elements found")
                        fail_status = "N/A"
                        real_count = 0

                    all_results.append({
                        "rule_set": name,
                        "rule": spec.name,
                        "status": "PASS" if spec.status is True else fail_status,
                        "elements_checked": total,
                    })

            # --- Cross-validation: TypeID <-> ClassCode ---
            st.subheader("\U0001F4CB Cross-validation: TypeID \u2194 ClassCode")
            mismatches = []
            walls = list(ifc_file.by_type("IfcWall"))
            for wall in walls:
                psets = ifcopenshell.util.element.get_psets(wall)
                jm = psets.get("JM", {})
                type_id = jm.get("TypeID", "")
                class_code = jm.get("ClassCodeBuildingElement", "")
                if not type_id or not class_code:
                    continue
                prefix = ""
                for p in TYPEID_CLASSCODE_MAP:
                    if type_id.startswith(p):
                        prefix = p
                        break
                if prefix:
                    expected = TYPEID_CLASSCODE_MAP[prefix]
                    if class_code != expected:
                        wall_name = wall.Name if hasattr(wall, 'Name') and wall.Name else "\u2014"
                        wall_guid = getattr(wall, 'GlobalId', None)
                        # Check exception
                        is_exc, exc_info = is_excepted(wall, ifc_file, "TypeID-ClassCode match", exceptions)
                        mismatches.append({
                            "ID": f"#{wall.id()}",
                            "Name": wall_name,
                            "TypeID": type_id,
                            "ClassCode": class_code,
                            "Expected": expected,
                            "_guid": wall_guid,
                            "_excepted": is_exc,
                            "_exc_info": exc_info,
                            "_entity": wall,
                        })

            real_mismatches = [m for m in mismatches if not m["_excepted"]]
            exc_mismatches = [m for m in mismatches if m["_excepted"]]

            if real_mismatches:
                with st.expander(f"\u274C **TypeID \u2194 ClassCode mismatch** \u2014 {len(real_mismatches)} walls{f' ({len(exc_mismatches)} approved)' if exc_mismatches else ''}", expanded=False):
                    display_rows = [{k: v for k, v in m.items() if not k.startswith("_")} for m in real_mismatches]
                    st.dataframe(display_rows, use_container_width=True, hide_index=True)
                all_results.append({
                    "rule_set": "Cross-validation",
                    "rule": "TypeID-ClassCode match",
                    "status": "FAIL",
                    "elements_checked": len(real_mismatches),
                })
                mismatch_guids = [m["_guid"] for m in real_mismatches if m["_guid"]]
                first_mismatch_entity = real_mismatches[0]["_entity"] if real_mismatches else None
                if mismatch_guids:
                    bcf_issues.append({
                        "title": "Cross-validation: TypeID-ClassCode mismatch",
                        "description": f"{len(real_mismatches)} walls (excl. {len(exc_mismatches)} exceptions)",
                        "guids": mismatch_guids,
                        "first_entity": first_mismatch_entity,
                    })
            else:
                label = f"all walls consistent"
                if exc_mismatches:
                    label += f" ({len(exc_mismatches)} via approved exceptions)"
                st.markdown(f"\u2705 **TypeID \u2194 ClassCode match** \u2014 {label}")
                all_results.append({
                    "rule_set": "Cross-validation",
                    "rule": "TypeID-ClassCode match",
                    "status": "PASS",
                    "elements_checked": len([w for w in walls if ifcopenshell.util.element.get_psets(w).get("JM", {}).get("TypeID")]),
                })

            # --- Tier 2: Advanced Checks ---
            st.subheader("\U0001F50D Tier 2 \u2014 Advanced Checks")

            def get_pset_value(element, pset_name, prop_name):
                psets = ifcopenshell.util.element.get_psets(element)
                return psets.get(pset_name, {}).get(prop_name)

            # Check 1: Storey Heights
            storeys = ifc_file.by_type("IfcBuildingStorey")
            if len(storeys) >= 2:
                sorted_storeys = sorted(storeys, key=lambda s: s.Elevation or 0)
                bad_heights = []
                for i in range(1, len(sorted_storeys)):
                    lower = sorted_storeys[i - 1]
                    upper = sorted_storeys[i]
                    delta = (upper.Elevation or 0) - (lower.Elevation or 0)
                    if delta < 2.4 or delta > 5.0:
                        bad_heights.append({
                            "From": lower.Name or "\u2014",
                            "To": upper.Name or "\u2014",
                            "Height": f"{delta:.1f}m",
                            "Expected": "2.4\u20135.0m",
                            "_guids": [lower.GlobalId, upper.GlobalId],
                            "_entity": lower,
                        })
                if bad_heights:
                    with st.expander(f"\u274C **Storey heights** \u2014 {len(bad_heights)} unusual", expanded=False):
                        st.dataframe([{k: v for k, v in h.items() if not k.startswith("_")} for h in bad_heights], use_container_width=True, hide_index=True)
                    guids = [g for h in bad_heights for g in h["_guids"] if g]
                    first_storey_entity = bad_heights[0]["_entity"] if bad_heights else None
                    if guids:
                        bcf_issues.append({
                            "title": "Unusual storey heights",
                            "description": f"{len(bad_heights)} storey pairs with unexpected height",
                            "guids": guids,
                            "first_entity": first_storey_entity,
                        })
                    all_results.append({"rule_set": "Advanced", "rule": "Storey heights", "status": "FAIL", "elements_checked": len(sorted_storeys)})
                else:
                    st.markdown(f"\u2705 **Storey heights** \u2014 all {len(sorted_storeys)} storeys within 2.4\u20135.0m")
                    all_results.append({"rule_set": "Advanced", "rule": "Storey heights", "status": "PASS", "elements_checked": len(sorted_storeys)})

            # Check 2: Unassigned elements
            unassigned = []
            for entity_type in ["IfcWall", "IfcDoor", "IfcWindow", "IfcSlab"]:
                for element in ifc_file.by_type(entity_type):
                    container = ifcopenshell.util.element.get_container(element)
                    if container is None:
                        unassigned.append(element)
            if unassigned:
                type_counts = {}
                for e in unassigned:
                    t = e.is_a()
                    type_counts[t] = type_counts.get(t, 0) + 1
                summary_text = ", ".join(f"{c}x {t}" for t, c in type_counts.items())
                with st.expander(f"\u274C **Unassigned elements** \u2014 {len(unassigned)} without storey", expanded=False):
                    st.text(summary_text)
                    rows = []
                    for e in unassigned[:30]:
                        rows.append({"ID": f"#{e.id()}", "Type": e.is_a(), "Name": getattr(e, 'Name', None) or "\u2014"})
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                guids = [e.GlobalId for e in unassigned if e.GlobalId][:50]
                first_unassigned = unassigned[0] if unassigned else None
                if guids:
                    bcf_issues.append({
                        "title": f"{len(unassigned)} elements not assigned to storey",
                        "description": summary_text,
                        "guids": guids,
                        "first_entity": first_unassigned,
                    })
                all_results.append({"rule_set": "Advanced", "rule": "Elements assigned to storey", "status": "FAIL", "elements_checked": len(unassigned)})
            else:
                st.markdown("\u2705 **Element assignment** \u2014 all elements assigned to a storey")
                all_results.append({"rule_set": "Advanced", "rule": "Elements assigned to storey", "status": "PASS", "elements_checked": 0})

            # Check 3: Spaces exist
            spaces = ifc_file.by_type("IfcSpace")
            if len(spaces) == 0:
                st.markdown("\u274C **Spaces/Rooms** \u2014 no IfcSpace found (critical for IDA ICE)")
                bcf_issues.append({
                    "title": "No rooms/spaces in model",
                    "description": "Zero IfcSpace elements. Critical for energy simulation and area calculations.",
                    "guids": [],
                    "first_entity": None,
                })
                all_results.append({"rule_set": "Advanced", "rule": "Spaces exist", "status": "FAIL", "elements_checked": 0})
            else:
                no_area = []
                unnamed = []
                for space in spaces:
                    area = get_pset_value(space, "Qto_SpaceBaseQuantities", "NetFloorArea")
                    if area is None or area <= 0:
                        no_area.append(space)
                    if not space.Name or space.Name.strip() == "":
                        unnamed.append(space)

                space_issues = []
                if no_area:
                    space_issues.append(f"{len(no_area)} without NetFloorArea")
                if unnamed:
                    space_issues.append(f"{len(unnamed)} without Name")

                if space_issues:
                    with st.expander(f"\u274C **Spaces** \u2014 {len(spaces)} found, issues: {', '.join(space_issues)}", expanded=False):
                        if no_area:
                            st.markdown(f"**Missing NetFloorArea:** {len(no_area)} spaces")
                            rows = [{"ID": f"#{s.id()}", "Name": s.Name or "\u2014", "LongName": getattr(s, 'LongName', None) or "\u2014"} for s in no_area[:20]]
                            st.dataframe(rows, use_container_width=True, hide_index=True)
                        if unnamed:
                            st.markdown(f"**Missing Name:** {len(unnamed)} spaces")
                    problem_spaces = no_area + unnamed
                    guids = [s.GlobalId for s in problem_spaces if s.GlobalId][:30]
                    first_space = problem_spaces[0] if problem_spaces else None
                    if guids:
                        bcf_issues.append({
                            "title": f"Space issues: {', '.join(space_issues)}",
                            "description": f"{len(spaces)} spaces total",
                            "guids": guids,
                            "first_entity": first_space,
                        })
                    all_results.append({"rule_set": "Advanced", "rule": "Space completeness", "status": "FAIL", "elements_checked": len(spaces)})
                else:
                    st.markdown(f"\u2705 **Spaces** \u2014 {len(spaces)} rooms, all with Name and NetFloorArea")
                    all_results.append({"rule_set": "Advanced", "rule": "Space completeness", "status": "PASS", "elements_checked": len(spaces)})

            # Check 4: Windows exist and are hosted
            windows = ifc_file.by_type("IfcWindow")
            if len(windows) == 0:
                st.markdown("\u274C **Windows** \u2014 no IfcWindow found (likely export error)")
                bcf_issues.append({
                    "title": "No windows in model",
                    "description": "Zero IfcWindow elements. Check Revit IFC export settings.",
                    "guids": [],
                    "first_entity": None,
                })
                all_results.append({"rule_set": "Advanced", "rule": "Windows present and hosted", "status": "FAIL", "elements_checked": 0})
            else:
                orphan_windows = [w for w in windows if not (hasattr(w, "FillsVoids") and w.FillsVoids)]
                if orphan_windows:
                    with st.expander(f"\u274C **Windows** \u2014 {len(orphan_windows)}/{len(windows)} not hosted in wall", expanded=False):
                        rows = [{"ID": f"#{w.id()}", "Name": getattr(w, 'Name', None) or "\u2014"} for w in orphan_windows[:20]]
                        st.dataframe(rows, use_container_width=True, hide_index=True)
                    guids = [w.GlobalId for w in orphan_windows if w.GlobalId][:30]
                    first_orphan_window = orphan_windows[0] if orphan_windows else None
                    if guids:
                        bcf_issues.append({
                            "title": f"{len(orphan_windows)} windows without host wall",
                            "description": "Missing IfcRelFillsElement",
                            "guids": guids,
                            "first_entity": first_orphan_window,
                        })
                    all_results.append({"rule_set": "Advanced", "rule": "Windows present and hosted", "status": "FAIL", "elements_checked": len(windows)})
                else:
                    st.markdown(f"\u2705 **Windows** \u2014 {len(windows)} windows, all hosted in walls")
                    all_results.append({"rule_set": "Advanced", "rule": "Windows present and hosted", "status": "PASS", "elements_checked": len(windows)})

            # Check 5: Doors hosted
            doors = ifc_file.by_type("IfcDoor")
            if doors:
                orphan_doors = [d for d in doors if not (hasattr(d, "FillsVoids") and d.FillsVoids)]
                if orphan_doors:
                    with st.expander(f"\u26A0\uFE0F **Doors** \u2014 {len(orphan_doors)}/{len(doors)} not hosted in wall", expanded=False):
                        rows = [{"ID": f"#{d.id()}", "Name": getattr(d, 'Name', None) or "\u2014"} for d in orphan_doors[:20]]
                        st.dataframe(rows, use_container_width=True, hide_index=True)
                    guids = [d.GlobalId for d in orphan_doors if d.GlobalId][:30]
                    first_orphan_door = orphan_doors[0] if orphan_doors else None
                    if guids:
                        bcf_issues.append({
                            "title": f"{len(orphan_doors)} doors without host wall",
                            "description": "Missing IfcRelFillsElement",
                            "guids": guids,
                            "first_entity": first_orphan_door,
                        })
                    all_results.append({"rule_set": "Advanced", "rule": "Doors hosted", "status": "FAIL", "elements_checked": len(doors)})
                else:
                    st.markdown(f"\u2705 **Doors** \u2014 {len(doors)} doors, all hosted in walls")
                    all_results.append({"rule_set": "Advanced", "rule": "Doors hosted", "status": "PASS", "elements_checked": len(doors)})

            # Check 6: Site coordinates
            sites = ifc_file.by_type("IfcSite")
            for site in sites:
                site_problems = []
                if not site.RefLatitude:
                    site_problems.append("RefLatitude missing")
                if not site.RefLongitude:
                    site_problems.append("RefLongitude missing")
                if site.RefElevation is None:
                    site_problems.append("RefElevation missing")
                if site.RefLatitude:
                    lat = site.RefLatitude[0] if site.RefLatitude else 0
                    if lat < 55 or lat > 69:
                        site_problems.append(f"Latitude {lat}\u00b0 outside Sweden (55-69\u00b0N)")
                if site.RefLongitude:
                    lon = site.RefLongitude[0] if site.RefLongitude else 0
                    if lon < 11 or lon > 24:
                        site_problems.append(f"Longitude {lon}\u00b0 outside Sweden (11-24\u00b0E)")

                if site_problems:
                    st.markdown(f"\u26A0\uFE0F **Site coordinates** \u2014 {'; '.join(site_problems)}")
                    bcf_issues.append({
                        "title": "Site coordinates issue",
                        "description": "; ".join(site_problems),
                        "guids": [site.GlobalId] if site.GlobalId else [],
                        "first_entity": site,
                    })
                    all_results.append({"rule_set": "Advanced", "rule": "Site coordinates", "status": "FAIL", "elements_checked": 1})
                else:
                    st.markdown(f"\u2705 **Site coordinates** \u2014 location set within Sweden")
                    all_results.append({"rule_set": "Advanced", "rule": "Site coordinates", "status": "PASS", "elements_checked": 1})

            # Check 7: Floor type in bathrooms
            bathroom_keywords = ["badrum", "bad", "wc", "toalett", "dusch"]
            expected_floor_type = "14"
            bathroom_floor_issues = []
            for space in spaces:
                space_name = ((space.Name or "") + " " + (getattr(space, 'LongName', None) or "")).lower()
                if not any(kw in space_name for kw in bathroom_keywords):
                    continue
                contained_slabs = []
                for rel in getattr(space, "ContainsElements", []):
                    for el in rel.RelatedElements:
                        if el.is_a("IfcSlab"):
                            contained_slabs.append(el)
                for rel in getattr(space, "BoundedBy", []):
                    el = rel.RelatedBuildingElement
                    if el and el.is_a("IfcSlab") and el not in contained_slabs:
                        contained_slabs.append(el)
                for slab in contained_slabs:
                    type_id = get_pset_value(slab, "JM", "TypeID") or ""
                    if type_id and type_id != expected_floor_type:
                        bathroom_floor_issues.append({
                            "Space": space.Name or "\u2014",
                            "Slab": getattr(slab, 'Name', None) or "\u2014",
                            "TypeID": type_id,
                            "Expected": expected_floor_type,
                            "_guids": [g for g in [slab.GlobalId, space.GlobalId] if g],
                            "_entity": slab,
                        })

            if bathroom_floor_issues:
                with st.expander(f"\u274C **Bathroom floor type** \u2014 {len(bathroom_floor_issues)} wrong", expanded=False):
                    st.dataframe([{k: v for k, v in i.items() if not k.startswith("_")} for i in bathroom_floor_issues], use_container_width=True, hide_index=True)
                guids = [g for i in bathroom_floor_issues for g in i["_guids"]]
                first_bathroom_entity = bathroom_floor_issues[0]["_entity"] if bathroom_floor_issues else None
                if guids:
                    bcf_issues.append({
                        "title": f"{len(bathroom_floor_issues)} bathroom floors with wrong TypeID",
                        "description": f"Expected TypeID {expected_floor_type}",
                        "guids": guids,
                        "first_entity": first_bathroom_entity,
                    })
                all_results.append({"rule_set": "Advanced", "rule": "Bathroom floor type", "status": "FAIL", "elements_checked": len(bathroom_floor_issues)})
            else:
                checked_bathrooms = sum(1 for s in spaces if any(kw in ((s.Name or "") + " " + (getattr(s, 'LongName', None) or "")).lower() for kw in bathroom_keywords))
                if checked_bathrooms > 0:
                    st.markdown(f"\u2705 **Bathroom floor type** \u2014 {checked_bathrooms} bathrooms checked")
                else:
                    st.markdown(f"\u26A0\uFE0F **Bathroom floor type** \u2014 no bathrooms found to check")
                all_results.append({"rule_set": "Advanced", "rule": "Bathroom floor type", "status": "PASS" if checked_bathrooms > 0 else "N/A", "elements_checked": checked_bathrooms})

            # --- Summary ---
            st.markdown("---")
            st.subheader("\U0001F4CA Summary")
            total_rules = len(all_results)
            passed = sum(1 for r in all_results if r["status"] == "PASS")
            failed_count = sum(1 for r in all_results if r["status"] == "FAIL")
            na = sum(1 for r in all_results if r["status"] == "N/A")

            col1, col2, col3 = st.columns(3)
            col1.metric("Passed", f"{passed}/{total_rules}")
            col2.metric("Failed", f"{failed_count}/{total_rules}")
            col3.metric("N/A", f"{na}/{total_rules}")

            st.session_state.last_results = all_results
            st.session_state.last_filename = uploaded_file.name
            st.session_state.last_timestamp = datetime.now().isoformat()
            st.session_state.last_bcf_issues = bcf_issues
            st.session_state.last_new_exceptions = new_exceptions
            st.session_state.last_ifc_file = ifc_file  # Keep reference for BCF export

        except Exception as e:
            st.error(f"Error during validation: {str(e)}")
            st.exception(e)
        finally:
            os.unlink(tmp_path)

    # --- Export ---
    if "last_results" in st.session_state:
        st.markdown("---")
        st.subheader("\U0001F4E5 Export Results")

        col_bcf, col_json, col_exc = st.columns(3)

        with col_bcf:
            bcf_issues = st.session_state.get("last_bcf_issues", [])
            ifc_file = st.session_state.get("last_ifc_file")
            if bcf_issues:
                try:
                    bcf_file = BcfXml.create_new("JM BIM Check")
                    for issue in bcf_issues:
                        topic = bcf_file.add_topic(
                            title=issue["title"],
                            description=issue["description"],
                            author="bim@jm.se",
                            topic_type="Error",
                            topic_status="Open",
                        )
                        if issue["guids"]:
                            try:
                                add_bcf_viewpoint(topic, issue, ifc_file)
                            except Exception:
                                pass
                    bcf_path = tempfile.mktemp(suffix=".bcf")
                    bcf_file.save(bcf_path)
                    with open(bcf_path, "rb") as f:
                        bcf_bytes = f.read()
                    os.unlink(bcf_path)
                    st.download_button(
                        "\U0001F4CB BCF (Solibri/Revit)",
                        data=bcf_bytes,
                        file_name=f"bim_check_{st.session_state.last_filename}_{datetime.now().strftime('%Y%m%d_%H%M')}.bcf",
                        mime="application/octet-stream",
                    )
                    st.caption(f"{len(bcf_issues)} issues")
                except Exception as e:
                    st.error(f"BCF failed: {e}")
            else:
                st.info("No failures \u2014 no BCF needed.")

        with col_json:
            export_data = {
                "file": st.session_state.last_filename,
                "timestamp": st.session_state.last_timestamp,
                "results": st.session_state.last_results,
            }
            st.download_button(
                "\U0001F4C4 JSON report",
                data=json.dumps(export_data, indent=2, ensure_ascii=False),
                file_name=f"bim_check_{st.session_state.last_filename}_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                mime="application/json",
            )

        with col_exc:
            new_exceptions = st.session_state.get("last_new_exceptions", [])
            if new_exceptions:
                # Deduplicate by TypeID + Rule
                seen = set()
                unique = []
                for exc in new_exceptions:
                    key = (exc["TypeID"], exc["Rule"])
                    if key not in seen:
                        seen.add(key)
                        unique.append(exc)
                df_exc = pd.DataFrame(unique, columns=["TypeID", "Rule", "ElementName", "ApprovedBy", "Date", "Reference", "Reason"])
                csv = df_exc.to_csv(index=False)
                st.download_button(
                    "\U0001F4DD Exception template",
                    data=csv,
                    file_name=f"exceptions_template_{st.session_state.last_filename}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                )
                st.caption(f"{len(unique)} failures to review")
            else:
                st.info("No failures to except.")


if __name__ == "__main__":
    main()
