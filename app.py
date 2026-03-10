import streamlit as st
import ifcopenshell
import ifctester
import ifctester.ids
import tempfile
import os
import json
from pathlib import Path
from datetime import datetime

# --- Config ---
IDS_FOLDER = Path("ids_files")
APP_TITLE = "JM BIM Checker"

# --- Simple auth ---
def check_password():
    """Simple password gate for prototype."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.title(f"🔒 {APP_TITLE}")
    st.markdown("Log in to access the BIM checker.")
    password = st.text_input("Password", type="password")
    if st.button("Log in"):
        # Change this password for your deployment
        if password == "jm2025":
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Wrong password.")
    return False


def load_ids_files():
    """Load all .ids files from the ids_files folder."""
    ids_files = {}
    if IDS_FOLDER.exists():
        for f in sorted(IDS_FOLDER.glob("*.ids")):
            try:
                ids_obj = ifctester.ids.open(str(f))
                ids_files[f.stem] = {"path": f, "ids": ids_obj}
            except Exception as e:
                st.warning(f"Could not load {f.name}: {e}")
    return ids_files


def run_validation(ifc_file, ids_obj):
    """Run IDS validation against an IFC file. Returns the ids object with results."""
    ids_obj.validate(ifc_file)
    return ids_obj


def extract_results(ids_obj):
    """Extract validation results into a structured format."""
    results = []
    for spec in ids_obj.specifications:
        status = spec.status
        applicable_count = len(spec.applicable_entities) if spec.applicable_entities else 0

        # Count pass/fail
        pass_count = 0
        fail_count = 0
        failed_entities = []

        if spec.applicable_entities:
            for entity in spec.applicable_entities:
                # Each requirement on each entity has a status
                entity_passed = True
                for req in spec.requirements:
                    # Check if this requirement failed for this entity
                    pass
                # Use the spec-level status logic
            pass

        # Simpler approach: use spec status directly
        if hasattr(spec, 'failed_entities'):
            failed_entities = spec.failed_entities
            fail_count = len(failed_entities)
            pass_count = applicable_count - fail_count

        result = {
            "rule": spec.name,
            "status": "✅ PASS" if status is True else ("❌ FAIL" if status is False else "⚠️ N/A"),
            "status_bool": status,
            "applicable": applicable_count,
            "description": spec.description if hasattr(spec, 'description') else "",
        }
        results.append(result)
    return results


def main():
    if not check_password():
        return

    # --- Sidebar ---
    with st.sidebar:
        st.title(APP_TITLE)
        st.markdown("---")
        st.markdown("**How to use:**")
        st.markdown(
            "1. Upload your IFC file\n"
            "2. Select which rule sets to check\n"
            "3. Click **Run Validation**\n"
            "4. Review results"
        )
        st.markdown("---")
        st.markdown(f"*Prototype v0.1*")

    # --- Main area ---
    st.title("🏗️ IFC Model Checker")
    st.markdown("Upload an IFC file and validate it against JM's BIM requirements.")

    # Load available IDS files
    ids_files = load_ids_files()
    if not ids_files:
        st.error("No IDS rule files found in the ids_files/ folder.")
        return

    # --- Upload ---
    uploaded_file = st.file_uploader("Upload IFC file", type=["ifc"])

    # --- Rule set selection ---
    st.subheader("Select rule sets")
    selected_ids = []
    cols = st.columns(2)
    for i, (name, data) in enumerate(ids_files.items()):
        col = cols[i % 2]
        with col:
            ids_obj = data["ids"]
            title = name.replace("_", " ")
            # Try to get info from IDS
            info_text = ""
            if hasattr(ids_obj, 'info') and ids_obj.info:
                if hasattr(ids_obj.info, 'description'):
                    info_text = ids_obj.info.description
            if st.checkbox(title, value=True, help=info_text):
                selected_ids.append((name, data))

    # --- Validate ---
    st.markdown("---")
    run_button = st.button("🚀 Run Validation", type="primary", disabled=uploaded_file is None)

    if run_button and uploaded_file is not None:
        # Save uploaded file to temp location
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
            tmp.write(uploaded_file.getvalue())
            tmp_path = tmp.name

        try:
            # Parse IFC
            with st.spinner("Parsing IFC file..."):
                ifc_file = ifcopenshell.open(tmp_path)

            st.success(
                f"Loaded **{uploaded_file.name}** — "
                f"Schema: {ifc_file.schema}, "
                f"Elements: {len(list(ifc_file))}"
            )

            # Run each selected IDS
            all_results = []
            for name, data in selected_ids:
                # Reload IDS fresh for each run (to reset state)
                ids_obj = ifctester.ids.open(str(data["path"]))

                with st.spinner(f"Checking: {name.replace('_', ' ')}..."):
                    ids_obj.validate(ifc_file)

                st.subheader(f"📋 {name.replace('_', ' ')}")

                # Process specifications
                for spec in ids_obj.specifications:
                    applicable = spec.applicable_entities if spec.applicable_entities else []
                    total = len(applicable)
                    failed = spec.failed_entities if spec.failed_entities else set()
                    fail_count = len(failed)
                    pass_count = total - fail_count

                    if spec.status is True:
                        st.markdown(f"✅ **{spec.name}** — {total} elements checked, all passed")
                    elif spec.status is False:
                        with st.expander(f"❌ **{spec.name}** — {fail_count}/{total} elements failed", expanded=False):
                            # Collect all failure reasons per entity
                            failure_details = {}
                            for req in spec.requirements:
                                if hasattr(req, 'failures') and req.failures:
                                    for failure in req.failures:
                                        eid = failure.element.id()
                                        if eid not in failure_details:
                                            entity = failure.element
                                            entity_name = entity.Name if hasattr(entity, 'Name') and entity.Name else "—"
                                            failure_details[eid] = {
                                                "type": entity.is_a(),
                                                "name": entity_name,
                                                "reasons": [],
                                            }
                                        failure_details[eid]["reasons"].append(failure.reason)

                            # Display as table
                            if failure_details:
                                rows = []
                                for eid, info in sorted(failure_details.items()):
                                    rows.append({
                                        "ID": f"#{eid}",
                                        "Type": info["type"],
                                        "Name": info["name"],
                                        "Reason": "; ".join(info["reasons"][:3]),
                                    })
                                st.dataframe(rows, use_container_width=True, hide_index=True)
                            else:
                                st.text("No detailed failure info available.")

                            if pass_count > 0:
                                st.markdown(f"*{pass_count} elements passed this check.*")
                    else:
                        st.markdown(f"⚠️ **{spec.name}** — No applicable elements found")

                    all_results.append({
                        "rule_set": name,
                        "rule": spec.name,
                        "status": "PASS" if spec.status is True else ("FAIL" if spec.status is False else "N/A"),
                        "elements_checked": total,
                    })

            # --- Summary ---
            st.markdown("---")
            st.subheader("📊 Summary")
            total_rules = len(all_results)
            passed = sum(1 for r in all_results if r["status"] == "PASS")
            failed = sum(1 for r in all_results if r["status"] == "FAIL")
            na = sum(1 for r in all_results if r["status"] == "N/A")

            col1, col2, col3 = st.columns(3)
            col1.metric("Passed", f"{passed}/{total_rules}", delta=None)
            col2.metric("Failed", f"{failed}/{total_rules}", delta=None)
            col3.metric("N/A", f"{na}/{total_rules}", delta=None)

            # Store results in session state for potential export
            st.session_state.last_results = all_results
            st.session_state.last_filename = uploaded_file.name
            st.session_state.last_timestamp = datetime.now().isoformat()

        except Exception as e:
            st.error(f"Error during validation: {str(e)}")
            st.exception(e)
        finally:
            os.unlink(tmp_path)

    # --- Export ---
    if "last_results" in st.session_state:
        st.markdown("---")
        export_data = {
            "file": st.session_state.last_filename,
            "timestamp": st.session_state.last_timestamp,
            "results": st.session_state.last_results,
        }
        st.download_button(
            "📥 Download results (JSON)",
            data=json.dumps(export_data, indent=2, ensure_ascii=False),
            file_name=f"bim_check_{st.session_state.last_filename}_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
        )


if __name__ == "__main__":
    main()
