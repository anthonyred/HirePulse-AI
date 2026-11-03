import os
import re
import json
import streamlit as st
import pypdf
import docx

# ---------------------------------------------------------
# Page Configuration & Styling
# ---------------------------------------------------------
st.set_page_config(
    page_title="HirePulse AI | Career & Technical Intelligence",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .reportview-container { background-color: #0e1117; }
    .stButton>button {
        background-color: #2563eb;
        color: white;
        border-radius: 6px;
        font-weight: 600;
        border: none;
    }
    .stButton>button:hover { background-color: #1d4ed8; }
    .pii-badge {
        background-color: #1e3a8a;
        color: #93c5fd;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 0.85em;
        font-family: monospace;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# PII (Personal Identifiable Information) Redaction Engine
# ---------------------------------------------------------
class PIISanitizer:
    @staticmethod
    def sanitize(text: str, custom_name: str = "") -> tuple[str, dict]:
        """
        Masks Phone, Email, URLs, Candidate Names, and Govt IDs.
        Returns the sanitized text and a summary dict of redactions.
        """
        redactions = {"emails": 0, "phones": 0, "urls": 0, "custom_names": 0}
        sanitized = text

        # 1. Custom Name Masking (if candidate name provided)
        if custom_name and len(custom_name.strip()) > 2:
            name_pattern = re.compile(re.escape(custom_name.strip()), re.IGNORECASE)
            matches = len(name_pattern.findall(sanitized))
            if matches:
                sanitized = name_pattern.sub("[CANDIDATE_NAME]", sanitized)
                redactions["custom_names"] += matches

        # 2. Email Masking
        email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
        emails_found = len(re.findall(email_pattern, sanitized))
        if emails_found:
            sanitized = re.sub(email_pattern, '[EMAIL_REDACTED]', sanitized)
            redactions["emails"] += emails_found

        # 3. Phone Numbers (Indian & Global formats: +91, 10-digit mobile, hyphenated)
        phone_pattern = r'(?:\+?91[\s-]?)?[6789]\d{9}|\b(?:\+?\d{1,3}[\s-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b'
        phones_found = len(re.findall(phone_pattern, sanitized))
        if phones_found:
            sanitized = re.sub(phone_pattern, '[PHONE_REDACTED]', sanitized)
            redactions["phones"] += phones_found

        # 4. LinkedIn & Personal Profiles URL Masking
        linkedin_pattern = r'linkedin\.com/in/[a-zA-Z0-9_-]+'
        if re.search(linkedin_pattern, sanitized, re.IGNORECASE):
            sanitized = re.sub(linkedin_pattern, 'linkedin.com/in/[PROFILE_REDACTED]', sanitized, flags=re.IGNORECASE)
            redactions["urls"] += 1

        return sanitized, redactions

# ---------------------------------------------------------
# Persistent Knowledge Base Helper Functions
# ---------------------------------------------------------
RUBRIC_FILE = os.path.join(os.path.dirname(__file__), "rubric_knowledge.json")

def load_rubric():
    if os.path.exists(RUBRIC_FILE):
        try:
            with open(RUBRIC_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "resume_rules": ["Mandate Google XYZ formula: Accomplished [X], as measured by [Y], by doing [Z].", "Explicitly state GPU VRAM, inference latency, and quantization formats."],
        "linkedin_rules": ["Stop posting generic course certificates; post technical teardowns of real optimizations.", "Headline must state what you build (e.g. AI Engineer | Local Multimodal Pipelines)."],
        "github_rules": ["Must include an architectural diagram (Mermaid.js).", "Must feature empirical benchmarks (latency, VRAM consumption)."],
        "cold_email_rules": ["Keep length strictly under 80 words.", "Lead with a concrete observation about their tech stack or public repository."],
        "interview_questions": []
    }

def save_rubric(data):
    with open(RUBRIC_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

rubric = load_rubric()

# ---------------------------------------------------------
# Document Parsers (PDF & DOCX)
# ---------------------------------------------------------
def parse_uploaded_file(uploaded_file):
    text = ""
    filename = uploaded_file.name.lower()
    if filename.endswith(".pdf"):
        reader = pypdf.PdfReader(uploaded_file)
        for page in reader.pages:
            text += page.extract_text() or ""
    elif filename.endswith(".docx"):
        doc = docx.Document(uploaded_file)
        for para in doc.paragraphs:
            text += para.text + "\n"
    return text.strip()

# ---------------------------------------------------------
# Dynamic Groq Model Discovery (No Hardcoded Breakages)
# ---------------------------------------------------------
def fetch_active_groq_models(api_key: str) -> list[str]:
    """Queries Groq API live to retrieve only active chat models for this API key."""
    if not api_key:
        return []
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        models_data = client.models.list()
        # Filter out audio/whisper/guard models, prioritize high-quality chat models
        active = []
        for m in models_data.data:
            mid = m.id.lower()
            if "whisper" not in mid and "guard" not in mid and "tts" not in mid:
                active.append(m.id)
        # Sort so conversational/reasoning models appear first
        active.sort(key=lambda x: (
            0 if "gpt-oss-120b" in x else
            1 if "qwen" in x else
            2 if "gpt-oss-20b" in x else
            3 if "llama-3.3" in x else
            4 if "llama" in x else 5
        ))
        return active
    except Exception:
        # Fallback defaults if list call fails
        return ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b", "llama-3.3-70b-versatile"]

# ---------------------------------------------------------
# LLM Inference Client (With Dynamic Self-Healing Retry)
# ---------------------------------------------------------
def query_llm(prompt: str, system_prompt: str = "You are an elite, brutally honest Principal AI Architect and Silicon Valley/Hyderabad Tech Recruiter.") -> str:
    backend = st.session_state.get("llm_backend", "Groq (Free Cloud)")
    api_key = st.session_state.get("groq_api_key", "").strip() or os.environ.get("GROQ_API_KEY", "")

    if backend == "Groq (Free Cloud)":
        if not api_key:
            return "⚠️ Please enter your free Groq API key in the left sidebar (get one at https://console.groq.com) to run evaluations."
        
        target_model = st.session_state.get("groq_model", "")
        
        try:
            from groq import Groq
            client = Groq(api_key=api_key)
            
            # If no model selected, dynamically fetch the first active one
            if not target_model:
                active_list = fetch_active_groq_models(api_key)
                target_model = active_list[0] if active_list else "openai/gpt-oss-120b"
                
            completion = client.chat.completions.create(
                model=target_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=2048
            )
            return completion.choices[0].message.content
            
        except Exception as e:
            err_msg = str(e)
            # Self-healing: if model is decommissioned or not found, query Groq live and auto-retry
            if any(k in err_msg.lower() for k in ["decommissioned", "not_found", "400", "404"]):
                try:
                    live_models = fetch_active_groq_models(api_key)
                    for fallback in live_models:
                        if fallback != target_model:
                            try:
                                retry_comp = client.chat.completions.create(
                                    model=fallback,
                                    messages=[
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": prompt}
                                    ],
                                    temperature=0.2,
                                    max_tokens=2048
                                )
                                # Update session state to the working model
                                st.session_state["groq_model"] = fallback
                                return f"*(Auto-switched to active model: `{fallback}`)*\n\n" + retry_comp.choices[0].message.content
                            except Exception:
                                continue
                except Exception as e2:
                    return f"❌ Groq API Auto-Switch Error: {str(e2)}"
                    
            return f"❌ Groq API Error: {err_msg}"
            
    elif backend == "Ollama (Local RTX 3050)":
        import requests
        ollama_url = st.session_state.get("ollama_url", "http://localhost:11434/api/generate")
        model = st.session_state.get("ollama_model", "llama3.2:3b")
        try:
            payload = {
                "model": model,
                "prompt": f"{system_prompt}\n\nTask:\n{prompt}",
                "stream": False
            }
            resp = requests.post(ollama_url, json=payload, timeout=90)
            if resp.status_code == 200:
                return resp.json().get("response", "No response returned.")
            else:
                return f"❌ Ollama Error (Status {resp.status_code}): {resp.text}"
        except Exception as e:
            return f"❌ Local Ollama Connection Failed: {str(e)}. Make sure Ollama is running (`ollama serve`)."

# ---------------------------------------------------------
# Sidebar Configuration
# ---------------------------------------------------------
with st.sidebar:
    st.title("⚡ HirePulse AI")
    st.caption("Career & Technical Intelligence Engine")
    st.markdown("---")
    
    st.subheader("🛡️ PII Protection Settings")
    enable_pii_filter = st.checkbox("Enable PII Anonymizer", value=True, help="Masks phone, email, full name, and personal links before transmitting to AI")
    custom_name = st.text_input("Candidate Name to Mask", value="Anthony Reddy", help="Exact name string to replace with [CANDIDATE_NAME]")
    
    st.markdown("---")
    st.subheader("1. LLM Backend Setup")
    llm_backend = st.selectbox(
        "Inference Engine",
        ["Groq (Free Cloud)", "Ollama (Local RTX 3050)"],
        key="llm_backend"
    )
    
    if llm_backend == "Groq (Free Cloud)":
        api_key_input = st.text_input("Groq API Key", type="password", key="groq_api_key", help="Get your free key at https://console.groq.com")
        
        # Dynamically fetch models from Groq live
        if api_key_input.strip():
            live_models = fetch_active_groq_models(api_key_input.strip())
            if live_models:
                st.selectbox("Active Models on Your Key (Live)", live_models, key="groq_model")
            else:
                st.text_input("Model ID", value="openai/gpt-oss-120b", key="groq_model")
        else:
            st.info("Paste your Groq API key above to load live active models.")
            st.selectbox("Model", ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b"], key="groq_model")
            
    else:
        st.text_input("Ollama Endpoint", value="http://localhost:11434/api/generate", key="ollama_url")
        st.text_input("Local Model Name", value="llama3.2:3b", key="ollama_model")
        st.info("💡 RTX 3050 Tip: `llama3.2:3b` uses ~2.2GB VRAM, allowing smooth inference.")
        
    st.markdown("---")
    st.subheader("Hardware & Location Context")
    st.markdown("""
    * **Machine**: ASUS ROG Strix G15
    * **GPU**: NVIDIA RTX 3050 (4GB VRAM)
    * **Storage**: `D:\\HirePulse AI`
    * **Target**: Hyderabad / Remote
    """)

# ---------------------------------------------------------
# Main Navigation Tabs
# ---------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "🔥 Brutal Hiring Bar Audit",
    "🧠 My Knowledge: Technical Interview Drill",
    "🎯 Job Matcher & Cold Pitcher",
    "📋 Real HR & Architect Rubrics"
])

# =========================================================
# TAB 1: BRUTAL HIRING BAR AUDIT
# =========================================================
with tab1:
    st.header("The Unfiltered Hiring Committee")
    st.markdown(
        "Simulates a joint panel consisting of a **No-BS Technical Recruiter** and a **Principal AI Architect**. "
        "Evaluates your assets strictly against real-world rejection criteria."
    )
    
    asset_type = st.radio(
        "Select Asset to Critique:",
        ["Resume (.pdf / .docx)", "LinkedIn Profile & Posts", "GitHub Repository", "Cold Outreach Email"],
        horizontal=True
    )
    
    content_to_review = ""
    
    if asset_type == "Resume (.pdf / .docx)":
        uploaded_resume = st.file_uploader("Upload Resume (.pdf or .docx)", type=["pdf", "docx"], key="resume_upload")
        if uploaded_resume:
            raw_text = parse_uploaded_file(uploaded_resume)
            if enable_pii_filter:
                content_to_review, red_stats = PIISanitizer.sanitize(raw_text, custom_name)
                st.info(f"🛡️ PII Filter Active: Redacted {red_stats['emails']} email(s), {red_stats['phones']} phone(s), {red_stats['custom_names']} name occurrence(s).")
            else:
                content_to_review = raw_text
                
            with st.expander("Preview Sanitized Text Sent to AI"):
                st.text(content_to_review[:1200] + ("..." if len(content_to_review) > 1200 else ""))
                
    elif asset_type == "LinkedIn Profile & Posts":
        raw_text = st.text_area(
            "Paste LinkedIn Headline, About Section, and Recent Post Content:",
            height=180,
            placeholder="Headline: Aspiring AI Engineer | Python | MongoDB\n\nRecent Post: Excited to announce I attended the HydPy meetup..."
        )
        if raw_text and enable_pii_filter:
            content_to_review, _ = PIISanitizer.sanitize(raw_text, custom_name)
        else:
            content_to_review = raw_text
        
    elif asset_type == "GitHub Repository":
        raw_text = st.text_area(
            "Paste Repository README or Project Architecture Details:",
            height=180,
            placeholder="Paste Session Scribe or DocForge README content here..."
        )
        content_to_review = raw_text
        
    elif asset_type == "Cold Outreach Email":
        raw_text = st.text_area(
            "Paste Drafted Cold Email Message:",
            height=140,
            placeholder="Subject: Inquiry for Junior AI Engineer Role...\nDear Sir, I am writing to express my interest..."
        )
        if raw_text and enable_pii_filter:
            content_to_review, _ = PIISanitizer.sanitize(raw_text, custom_name)
        else:
            content_to_review = raw_text

    if st.button("Run Brutal Audit", key="run_audit_btn"):
        if not content_to_review.strip():
            st.error("Please provide or upload content before running the audit.")
        else:
            with st.spinner("Hiring Committee is tearing apart your submission..."):
                active_rules = rubric.get(
                    "resume_rules" if "Resume" in asset_type else
                    "linkedin_rules" if "LinkedIn" in asset_type else
                    "github_rules" if "GitHub" in asset_type else
                    "cold_email_rules", []
                )
                rules_str = "\n- ".join(active_rules)
                
                audit_prompt = f"""
                You are conducting a brutal, unfiltered hiring bar audit for an entry-level candidate applying for a Junior AI Engineer position in Hyderabad or Remote.
                
                Candidate Hardware & Projects Context:
                - ASUS ROG Strix (AMD Ryzen 7, NVIDIA RTX 3050 4GB VRAM).
                - Projects: 'SessionScribe' (offline faster-whisper float16 pipeline), 'DocForge' (Pandoc document automation), 4.5 years at TCS as SAP Database Lead.
                
                Rules & Rejection Criteria Mandated by Real HRs & Architects:
                - {rules_str}
                
                Target Asset to Review ({asset_type}) [PII Sanitized]:
                \"\"\"
                {content_to_review}
                \"\"\"
                
                Provide your critique in structured Markdown following this EXACT layout:
                
                ### 1. Overall Hiring Bar Score: [Score out of 100]
                **Verdict**: [Strong Reject / Weak Reject / Borderline / Interview Pass]
                
                ### 2. Fatal Red Flags (Why a Recruiter/Architect Rejects This in 10 Seconds)
                List specific, unforgiving bullet points. Call out passive voice, lack of metrics, cliché tutorial claims, or student-like language.
                
                ### 3. Technical Credibility Gaps
                What engineering realities are missing? (e.g. latency numbers, VRAM budget, VAD filtering, error handling, Docker, indexing).
                
                ### 4. Line-by-Line Brutal Rewrite
                Take the weakest section or sentence and provide the exact, production-ready replacement text that an AI Architect would respect.
                """
                
                response = query_llm(audit_prompt)
                st.markdown(response)

# =========================================================
# TAB 2: MY KNOWLEDGE - TECHNICAL INTERVIEW DRILL
# =========================================================
with tab2:
    st.header("🧠 My Knowledge: Technical Interview Drill")
    st.markdown(
        "Real technical interviews test system-level trade-offs, edge-case failure modes, and hardware budgeting. "
        "Practice your answers and receive architectural grading from a Principal AI Architect."
    )
    
    questions_list = rubric.get("interview_questions", [])
    domains = list(set([q.get("domain", "General AI") for q in questions_list])) or ["General AI"]
    
    col1, col2 = st.columns([1, 2])
    with col1:
        selected_domain = st.selectbox("Select Technical Domain:", domains)
        filtered_questions = [q for q in questions_list if q.get("domain") == selected_domain]
        
        q_options = [q["question"] for q in filtered_questions]
        selected_q_text = st.selectbox("Select Question:", q_options if q_options else ["No questions in domain"])
        
        target_q = next((q for q in filtered_questions if q["question"] == selected_q_text), None)
        
        if target_q and st.checkbox("Show Architect's Grading Focus"):
            st.info(f"**Key Concepts Required**: {target_q.get('architect_key_points')}")
            
    with col2:
        st.subheader("Your Architectural Answer")
        user_answer = st.text_area(
            "Explain your solution, architecture, and trade-offs:",
            height=200,
            placeholder="Type your detailed explanation or Python implementation here..."
        )
        
        if st.button("Submit Answer for Architect Grading", key="grade_answer_btn"):
            if not user_answer.strip():
                st.warning("Please type your answer before submitting.")
            else:
                with st.spinner("Principal Architect is reviewing your response..."):
                    eval_prompt = f"""
                    You are a Principal AI Architect interviewing a candidate for a Junior AI Engineer role.
                    The candidate is working with an NVIDIA RTX 3050 (4GB VRAM), Python, faster-whisper, and MongoDB.
                    
                    Question:
                    {selected_q_text}
                    
                    Architect Reference Concepts:
                    {target_q.get('architect_key_points', 'Evaluate for deep technical accuracy.')}
                    
                    Candidate's Answer:
                    \"\"\"
                    {user_answer}
                    \"\"\"
                    
                    Grade this response constructively but rigorously:
                    1. **Score (1 to 10)**: Strict rating based on production engineering standards.
                    2. **What Was Handled Well**: Specific correct architectural claims.
                    3. **Critical Gaps & Misconceptions**: Did they miss latency trade-offs, OOM risks, concurrency, or computational complexity?
                    4. **The Model Architect-Level Answer**: Provide a concise, high-signal response showing how a senior engineer would answer in an interview.
                    """
                    grade_res = query_llm(eval_prompt)
                    st.markdown(grade_res)

# =========================================================
# TAB 3: JOB MATCHER & COLD PITCH GENERATOR
# =========================================================
with tab3:
    st.header("🎯 Job Matcher & Technical Outreach Engine")
    st.markdown("Match your resume directly against target job descriptions and generate tailored outreach.")
    
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("1. Your Current Resume")
        jd_resume_file = st.file_uploader("Upload Resume (.pdf/.docx)", type=["pdf", "docx"], key="jd_resume")
        jd_resume_text = ""
        if jd_resume_file:
            raw_jd_res = parse_uploaded_file(jd_resume_file)
            if enable_pii_filter:
                jd_resume_text, _ = PIISanitizer.sanitize(raw_jd_res, custom_name)
                st.caption("🛡️ Resume sanitized before analysis.")
            else:
                jd_resume_text = raw_jd_res
            st.caption(f"Loaded {len(jd_resume_text.split())} words.")
            
    with col_b:
        st.subheader("2. Target Job Description")
        jd_text = st.text_area(
            "Paste Job Description (Hasjob, Wellfound, LinkedIn):",
            height=180,
            placeholder="Paste role requirements, tech stack, and responsibilities..."
        )
        
    company_name = st.text_input("Company Name / Team:", placeholder="e.g. T-Hub AI Startup, Tech Lead at Thoughtworks")
    
    if st.button("Generate Match Audit & Cold Pitch", key="match_btn"):
        if not jd_resume_text or not jd_text:
            st.error("Please provide both your resume and the target job description.")
        else:
            with st.spinner("Analyzing keyword density and drafting outreach pitch..."):
                match_prompt = f"""
                You are a Technical Sourcing Specialist and AI Engineer.
                
                Resume [Sanitized]:
                \"\"\"
                {jd_resume_text[:2500]}
                \"\"\"
                
                Target Job Description:
                \"\"\"
                {jd_text[:2500]}
                \"\"\"
                
                Company / Team: {company_name or 'the team'}
                
                Candidate Context:
                - ASUS ROG Strix G15 (AMD Ryzen 7, NVIDIA RTX 3050 4GB VRAM).
                - SessionScribe (faster-whisper, float16, local VAD audio processing).
                - DocForge (Pandoc automated markdown-to-docx compilation).
                - 4.5 years at TCS as SAP Database Lead.
                
                Provide:
                1. **ATS Match Percentage**: Estimated 0-100% score based on hard skill overlap.
                2. **Top 3 Missing Skills / Keywords**: Critical tools in the JD not clearly highlighted in the resume.
                3. **Recommended Highlight**: Exactly which part of SessionScribe or DocForge to emphasize for this role.
                4. **High-Conversion 75-Word Cold Outreach Email**:
                   - Direct subject line.
                   - Clear hook referencing their tech stack.
                   - Value proposition showing local 4GB VRAM optimization.
                   - Low-friction 2-minute call to action.
                """
                match_result = query_llm(match_prompt)
                st.markdown(match_result)

# =========================================================
# TAB 4: REAL HR & ARCHITECT RUBRICS KNOWLEDGE BASE
# =========================================================
with tab4:
    st.header("📋 Real-Life HR & Architect Knowledge Manager")
    st.markdown(
        "Update the hiring bar rules whenever you receive feedback from conversations with real HRs, "
        "AI architects, CTOs, and hiring managers in Hyderabad or online."
    )
    
    with st.expander("➕ Add New Real-World Feedback / Rejection Rule", expanded=True):
        feedback_category = st.selectbox(
            "Category:",
            ["Resume Rule", "LinkedIn Rule", "GitHub Rule", "Cold Email Rule", "Technical Interview Question"]
        )
        
        if feedback_category == "Technical Interview Question":
            new_domain = st.text_input("Technical Domain:", placeholder="e.g. Quantization, MongoDB Vector Search, FastAPI")
            new_question = st.text_area("Question asked in real interview:", placeholder="What happens when...")
            new_points = st.text_area("Architect's Expected Key Points:", placeholder="Must mention INT8, cache locality...")
            
            if st.button("Save New Interview Question"):
                if new_question.strip():
                    rubric["interview_questions"].append({
                        "domain": new_domain.strip() or "General AI",
                        "question": new_question.strip(),
                        "architect_key_points": new_points.strip()
                    })
                    save_rubric(rubric)
                    st.success("New interview question added to your knowledge base!")
                    st.rerun()
        else:
            new_rule = st.text_input("Enter exact feedback / rule received from HR or Architect:")
            if st.button("Save New Rubric Rule"):
                if new_rule.strip():
                    key_map = {
                        "Resume Rule": "resume_rules",
                        "LinkedIn Rule": "linkedin_rules",
                        "GitHub Rule": "github_rules",
                        "Cold Email Rule": "cold_email_rules"
                    }
                    rubric[key_map[feedback_category]].append(new_rule.strip())
                    save_rubric(rubric)
                    st.success(f"Added new rule under {feedback_category}!")
                    st.rerun()
                    
    st.markdown("---")
    st.subheader("Current Active Rules in Knowledge Base")
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Resume Evaluation Criteria:**")
        for i, r in enumerate(rubric.get("resume_rules", [])):
            st.markdown(f"- {r}")
            
        st.markdown("**GitHub Evaluation Criteria:**")
        for i, r in enumerate(rubric.get("github_rules", [])):
            st.markdown(f"- {r}")
            
    with c2:
        st.markdown("**LinkedIn Evaluation Criteria:**")
        for i, r in enumerate(rubric.get("linkedin_rules", [])):
            st.markdown(f"- {r}")
            
        st.markdown("**Cold Email Evaluation Criteria:**")
        for i, r in enumerate(rubric.get("cold_email_rules", [])):
            st.markdown(f"- {r}")