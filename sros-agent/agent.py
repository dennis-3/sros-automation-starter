import os
import time
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from tools import get_lab_inventory, check_sros_state, get_sros_interfaces

load_dotenv()

class SROSAgent:
    def __init__(self, notes_path="./data"):
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash-lite", 
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            max_retries=6,  # Automatically retry on 429 errors
            temperature=0
        ).bind_tools([get_lab_inventory, check_sros_state, get_sros_interfaces])
        
        self.notes_path = notes_path
        self.index_path = "faiss_index"
        
        # Initialize Embeddings model
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=os.getenv("GOOGLE_API_KEY")
        )
        
        self.vector_db = self._initialize_rag()

    def _initialize_rag(self):
        """Builds or loads the Knowledge Base with rate-limiting and persistence."""
        
        # 1. Try to load from local disk first to save quota
        if os.path.exists(self.index_path):
            print("🚀 Loading existing RAG index from disk...")
            return FAISS.load_local(
                self.index_path, 
                self.embeddings, 
                allow_dangerous_deserialization=True
            )

        # 2. If no index, start the build process
        print("📂 No index found. Building new Knowledge Base...")
        if not os.path.exists(self.notes_path):
            os.makedirs(self.notes_path)
            
        loader = DirectoryLoader(self.notes_path, glob="**/*.md", loader_cls=TextLoader)
        docs = loader.load()
        
        if not docs:
            print("⚠️ No documents found in data folder.")
            return None

        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = splitter.split_documents(docs)
        
        print(f"🧩 Split into {len(chunks)} chunks. Starting 'Slow-Drip' indexing...")

        # 3. Slow-Drip Initialization (Trickle chunks to avoid 429)
        # Initialize with the first chunk
        vector_db = FAISS.from_documents([chunks[0]], self.embeddings)
        time.sleep(2.0) # Initial breather

        for i, chunk in enumerate(chunks[1:], 1):
            try:
                vector_db.add_documents([chunk])
                print(f"✅ Indexed chunk {i+1}/{len(chunks)}")
                time.sleep(3.0) # The delay to keep Free Tier happy (20 RPM)
            except Exception as e:
                if "429" in str(e):
                    print(f"🛑 Rate limit hit. Sleeping for 40s...")
                    time.sleep(40)
                    vector_db.add_documents([chunk])
                else:
                    print(f"❌ Error at chunk {i}: {e}")

        # 4. Save to disk so we never have to pay the 'token tax' for these notes again
        print(f"💾 Saving index to '{self.index_path}'...")
        vector_db.save_local(self.index_path)
        
        return vector_db

    def ask(self, query: str):
        context_text = ""
        if self.vector_db:
            # k=1 to keep context small and stay under token limits
            context_docs = self.vector_db.similarity_search(query, k=1)
            context_text = "\n".join([d.page_content for d in context_docs])
        
        messages = [
            SystemMessage(content=(
                "You are a Nokia SROS Network Architect. "
                "Use tools for live topology/state and context for design rules.\n"
                f"Context from notes:\n{context_text}"
            )),
            HumanMessage(content=query)
        ]
        
        try:
            response = self.llm.invoke(messages)
            
            if response.tool_calls:
                messages.append(response)
                
                for tool_call in response.tool_calls:
                    tool_map = {
                        "get_lab_inventory": get_lab_inventory, 
                        "check_sros_state": check_sros_state,
                        "get_sros_interfaces": get_sros_interfaces
                    }
                    
                    tool_name = tool_call["name"].lower()
                    selected_tool = tool_map.get(tool_name)
                    
                    if selected_tool:
                        print(f"DEBUG: Executing {tool_name}...") 
                        observation = selected_tool.invoke(tool_call["args"])
                        
                        messages.append(ToolMessage(
                            content=str(observation), 
                            tool_call_id=tool_call["id"]
                        ))
                
                final_response = self.llm.invoke(messages)
                return final_response.content
                
            return response.content

        except Exception as e:
            return f"Automation Error: {str(e)}"

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    notes_dir = os.path.join(script_dir, "data")
    
    bot = SROSAgent(notes_path=notes_dir)
    print("\n--- Agent Ready ---")
    print(bot.ask("What do my notes say about the management IP of R1?"))