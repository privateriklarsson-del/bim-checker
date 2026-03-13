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
                            for eid, info in real_failures.items():
                                guid = getattr(info["entity"], 'GlobalId', None)
                                if guid:
                                    guids.append(guid)
                            if guids:
                                bcf_issues.append({
                                    "title": f"{name}: {spec.name}",
                                    "description": f"{real_count} elements failed (excl. {exc_count} exceptions). Rule set: {name}",
                                    "guids": guids,
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
                if mismatch_guids:
                    bcf_issues.append({
                        "title": "Cross-validation: TypeID-ClassCode mismatch",
                        "description": f"{len(real_mismatches)} walls (excl. {len(exc_mismatches)} exceptions)",
                        "guids": mismatch_guids,
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
                                topic.add_viewpoint_from_point_and_guids(
                                    np.array([0.0, 0.0, 0.0]),
                                    *issue["guids"]
                                )
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
