import streamlit as st
import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.placement
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
from checks.registry import run_all_checks


def get_element_position(element):
    """Get the approximate XYZ position of an IFC element for BCF viewpoints."""
    try:
        if hasattr(element, 'ObjectPlacement') and element.ObjectPlacement:
            matrix = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
            return np.array([matrix[0][3], matrix[1][3], matrix[2][3]])
    except Exception:
        pass
    return np.array([0.0, 0.0, 0.0])


def add_bcf_topic(bcf_file, title, description, elements, severity="Error"):
    """Add a BCF topic with proper viewpoint positioned at the first element."""
    topic = bcf_file.add_topic(
        title=title,
        description=description,
        author="bim@jm.se",
        topic_type=severity,
        topic_status="Open",
    )
    guids = [getattr(e, 'GlobalId', None) for e in elements if getattr(e, 'GlobalId', None)]
    if guids:
        # Get position of first element for camera
        pos = get_element_position(elements[0])
        try:
            topic.add_viewpoint_from_point_and_guids(pos, *guids)
        except Exception:
            # Fallback to origin if positioning fails
            try:
                topic.add_viewpoint_from_point_and_guids(np.array([0.0, 0.0, 0.0]), *guids)
            except Exception:
                pass
    return topic


def generate_bcf(issues, project_name="JM BIM Check"):
    """Generate BCF bytes from a list of issues.
    
    For issues with multiple elements:
    - One summary topic selecting all elements (overview)
    - One topic per element with individual viewpoint (fix list)
    """
    if not issues:
        return None
    bcf_file = BcfXml.create_new(project_name)
    for issue in issues:
        elements = issue.get("elements", [])
        title = issue["title"]
        description = issue["description"]
        severity = issue.get("severity", "Error")

        if len(elements) > 1:
            # Summary topic: selects all elements at once
            add_bcf_topic(
                bcf_file,
                title=f"[SUMMARY] {title} ({len(elements)} elements)",
                description=description,
                elements=elements,
                severity=severity,
            )
            # Individual topics: one per element
            for element in elements:
                elem_name = getattr(element, 'Name', None) or f"#{element.id()}"
                add_bcf_topic(
                    bcf_file,
                    title=f"{title}: {elem_name}",
                    description=f"{description}\nElement: {elem_name} ({element.is_a()}) #{element.id()}",
                    elements=[element],
                    severity=severity,
                )
        elif len(elements) == 1:
            # Single element: just one topic
            elem_name = getattr(elements[0], 'Name', None) or f"#{elements[0].id()}"
            add_bcf_topic(
                bcf_file,
                title=f"{title}: {elem_name}",
                description=description,
                elements=elements,
                severity=severity,
            )
        else:
            # No elements (e.g. "no spaces found"): topic without viewpoint
            add_bcf_topic(
                bcf_file,
                title=title,
                description=description,
                elements=[],
                severity=severity,
            )

    bcf_path = tempfile.mktemp(suffix=".bcf")
    bcf_file.save(bcf_path)
    with open(bcf_path, "rb") as f:
        bcf_bytes = f.read()
    os.unlink(bcf_path)
    return bcf_bytes

# --- Config ---
IDS_FOLDER = Path("ids_files")
APP_TITLE = "JM BIM Checker"


# --- Exceptions handling ---
def load_exceptions(uploaded_exc):
    exceptions = {}
    if uploaded_exc is None:
        return exceptions
    try:
        if uploaded_exc.name.endswith(".csv"):
            df = pd.read_csv(uploaded_exc)
        else:
            df = pd.read_excel(uploaded_exc)
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
    if not exceptions:
        return False, None
    psets = ifcopenshell.util.element.get_psets(entity)
    jm = psets.get("JM", {})
    type_id = jm.get("TypeID", "")
    if not type_id:
        return False, None
    key = (type_id, rule_name)
    if key in exceptions:
        return True, exceptions[key]
    key_wild = (type_id, "*")
    if key_wild in exceptions:
        return True, exceptions[key_wild]
    return False, None


# --- Auth ---
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
        st.markdown(
            "**How to use:**\n"
            "1. Upload IFC file\n"
            "2. Optionally upload exceptions\n"
            "3. Select rule sets + advanced checks\n"
            "4. Click **Run Validation**\n"
            "5. Review results, download BCF"
        )
        st.markdown("---")
        st.markdown("*Prototype v0.3*")

    # --- Main ---
    st.title("\U0001F3D7\uFE0F IFC Model Checker")
    st.markdown("Upload an IFC file and validate it against JM's BIM requirements.")

    ids_files = load_ids_files()
    if not ids_files:
        st.error("No IDS rule files found.")
        return

    uploaded_file = st.file_uploader("Upload IFC file", type=["ifc"])
    st.caption("\U0001F4CC File naming convention: **D-PP-V-NN.ifc** \u2014 e.g. `A-40-V-02.ifc`")

    # Rule set selection
    st.subheader("Tier 1 \u2014 IDS Rule Sets")
    selected_ids = []
    cols = st.columns(2)
    for i, (name, data) in enumerate(ids_files.items()):
        col = cols[i % 2]
        with col:
            title = name.replace("_", " ")
            if st.checkbox(title, value=True):
                selected_ids.append((name, data))

    # Advanced checks toggle
    st.subheader("Tier 2 \u2014 Advanced Checks")
    run_advanced = st.checkbox("Run advanced checks (spaces, storeys, hosted elements, site, relational)", value=True)

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
            bcf_issues_tier1 = []
            bcf_issues_tier2 = []
            new_exceptions = []

            # =============================================
            # TIER 1: IDS Validation
            # =============================================
            for name, data in selected_ids:
                ids_obj = ifctester.ids.open(str(data["path"]))
                with st.spinner(f"Tier 1: {name.replace('_', ' ')}..."):
                    ids_obj.validate(ifc_file)

                st.subheader(f"\U0001F4CB Tier 1 \u2014 {name.replace('_', ' ')}")

                for spec in ids_obj.specifications:
                    applicable = spec.applicable_entities if spec.applicable_entities else []
                    total = len(applicable)
                    failed = spec.failed_entities if spec.failed_entities else set()

                    if spec.status is True:
                        st.markdown(f"\u2705 **{spec.name}** \u2014 {total} checked, all passed")
                        all_results.append({"rule_set": name, "rule": spec.name, "status": "PASS", "elements_checked": total})
                    elif spec.status is False:
                        # Split real failures vs exceptions
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
                                    item = {"type": entity.is_a(), "name": entity_name, "type_id": type_id, "reasons": [], "entity": entity}
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

                        if real_count > 0:
                            with st.expander(f"\u274C **{spec.name}** \u2014 {real_count} failed{f', {exc_count} approved' if exc_count else ''}", expanded=False):
                                rows = [{"ID": f"#{eid}", "Type": info["type"], "Name": info["name"], "TypeID": info["type_id"], "Reason": "; ".join(info["reasons"][:3])} for eid, info in sorted(real_failures.items())]
                                st.dataframe(rows, use_container_width=True, hide_index=True)
                                for eid, info in real_failures.items():
                                    if info["type_id"]:
                                        new_exceptions.append({"TypeID": info["type_id"], "Rule": spec.name, "ElementName": info["name"], "ApprovedBy": "", "Date": "", "Reference": "", "Reason": ""})
                            guids = [getattr(info["entity"], 'GlobalId', None) for info in real_failures.values()]
                            guids = [g for g in guids if g]
                            if guids:
                                bcf_tier1_issues.append({
                                    "title": f"{spec.name}",
                                    "description": f"{real_count} elements failed. Rule set: {name}",
                                    "elements": [info["entity"] for info in real_failures.values()],
                                    "severity": "Error",
                                })
                        elif exc_count > 0:
                            st.markdown(f"\u2705 **{spec.name}** \u2014 all passed ({exc_count} via exceptions)")

                        if exc_count > 0:
                            with st.expander(f"\u26A0\uFE0F Approved exceptions for {spec.name} \u2014 {exc_count}", expanded=False):
                                exc_rows = [{"ID": f"#{eid}", "TypeID": info["type_id"], "Name": info["name"], "Approved By": info["exception"].get("approved_by", ""), "Reference": info["exception"].get("reference", "")} for eid, info in sorted(excepted_items.items())]
                                st.dataframe(exc_rows, use_container_width=True, hide_index=True)

                        all_results.append({"rule_set": name, "rule": spec.name, "status": "FAIL" if real_count > 0 else "PASS", "elements_checked": total})
                    else:
                        st.markdown(f"\u26A0\uFE0F **{spec.name}** \u2014 No applicable elements found")
                        all_results.append({"rule_set": name, "rule": spec.name, "status": "N/A", "elements_checked": 0})

            # =============================================
            # TIER 2: Advanced Checks (framework)
            # =============================================
            if run_advanced:
                with st.spinner("Running Tier 2 advanced checks..."):
                    tier2_results = run_all_checks(ifc_file)

                for tier_name, check_results in tier2_results.items():
                    st.subheader(f"\U0001F50D {tier_name}")

                    for check_name, issues in check_results:
                        if not issues:
                            st.markdown(f"\u2705 **{check_name}** \u2014 OK")
                            all_results.append({"rule_set": tier_name, "rule": check_name, "status": "PASS", "elements_checked": 0})
                        else:
                            for issue in issues:
                                icon = "\u274C" if issue["severity"] == "Error" else "\u26A0\uFE0F"
                                elements = issue.get("elements", [])

                                if elements:
                                    with st.expander(f"{icon} **{issue['title']}**", expanded=False):
                                        st.markdown(issue["description"])
                                        rows = []
                                        for e in elements[:30]:
                                            name = getattr(e, 'Name', None) or "\u2014"
                                            rows.append({"ID": f"#{e.id()}", "Type": e.is_a(), "Name": name})
                                        if rows:
                                            st.dataframe(rows, use_container_width=True, hide_index=True)
                                else:
                                    st.markdown(f"{icon} **{issue['title']}** \u2014 {issue['description']}")

                                # Add to BCF Tier 2
                                bcf_tier2_issues.append({
                                    "title": f"{issue['title']}",
                                    "description": issue["description"],
                                    "elements": issue.get("elements", []),
                                    "severity": issue["severity"],
                                })

                            all_results.append({"rule_set": tier_name, "rule": check_name, "status": "FAIL", "elements_checked": len(issues)})

            # =============================================
            # SUMMARY
            # =============================================
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
            st.session_state.last_bcf_tier1 = bcf_tier1_issues
            st.session_state.last_bcf_tier2 = bcf_tier2_issues
            st.session_state.last_new_exceptions = new_exceptions

        except Exception as e:
            st.error(f"Error: {str(e)}")
            st.exception(e)
        finally:
            os.unlink(tmp_path)

    # =============================================
    # EXPORT
    # =============================================
    if "last_results" in st.session_state:
        st.markdown("---")
        st.subheader("\U0001F4E5 Export Results")

        col_bcf1, col_bcf2, col_json, col_exc = st.columns(4)

        fname = st.session_state.last_filename
        ts = datetime.now().strftime('%Y%m%d_%H%M')

        # BCF Tier 1
        with col_bcf1:
            tier1_issues = st.session_state.get("last_bcf_tier1", [])
            if tier1_issues:
                try:
                    bcf_bytes = generate_bcf(tier1_issues, "JM Tier 1 \u2014 IDS")
                    if bcf_bytes:
                        st.download_button(
                            "\U0001F4CB Tier 1 BCF",
                            data=bcf_bytes,
                            file_name=f"tier1_ids_{fname}_{ts}.bcf",
                            mime="application/octet-stream",
                        )
                        st.caption(f"{len(tier1_issues)} IDS issues")
                except Exception as e:
                    st.error(f"Tier 1 BCF failed: {e}")
            else:
                st.info("Tier 1: no issues")

        # BCF Tier 2
        with col_bcf2:
            tier2_issues = st.session_state.get("last_bcf_tier2", [])
            if tier2_issues:
                try:
                    bcf_bytes = generate_bcf(tier2_issues, "JM Tier 2 \u2014 Advanced")
                    if bcf_bytes:
                        st.download_button(
                            "\U0001F50D Tier 2 BCF",
                            data=bcf_bytes,
                            file_name=f"tier2_advanced_{fname}_{ts}.bcf",
                            mime="application/octet-stream",
                        )
                        st.caption(f"{len(tier2_issues)} advanced issues")
                except Exception as e:
                    st.error(f"Tier 2 BCF failed: {e}")
            else:
                st.info("Tier 2: no issues")

        # JSON
        with col_json:
            export_data = {
                "file": st.session_state.last_filename,
                "timestamp": st.session_state.last_timestamp,
                "results": st.session_state.last_results,
            }
            st.download_button(
                "\U0001F4C4 JSON report",
                data=json.dumps(export_data, indent=2, ensure_ascii=False),
                file_name=f"bim_check_{fname}_{ts}.json",
                mime="application/json",
            )

        # Exceptions template
        with col_exc:
            new_exceptions = st.session_state.get("last_new_exceptions", [])
            if new_exceptions:
                seen = set()
                unique = []
                for exc in new_exceptions:
                    key = (exc["TypeID"], exc["Rule"])
                    if key not in seen:
                        seen.add(key)
                        unique.append(exc)
                df_exc = pd.DataFrame(unique)
                st.download_button(
                    "\U0001F4DD Exceptions",
                    data=df_exc.to_csv(index=False),
                    file_name=f"exceptions_{ts}.csv",
                    mime="text/csv",
                )
                st.caption(f"{len(unique)} to review")
            else:
                st.info("No exceptions")


if __name__ == "__main__":
    main()
