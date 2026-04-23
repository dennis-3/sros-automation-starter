import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_core.messages import HumanMessage, SystemMessage
from tools import get_lab_inventory, check_sros_state

load_dotenv()

class SROSAgent:
    def __init__(self, notes_path="./data"):
        # Use the most stable naming convention for 2026
        self.llm = ChatGoogleGenerativeAI(
            model="models/gemini-1.5-flash-latest", # Use the 'latest' or '-001' suffix
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            temperature=0
        ).bind_tools([get_lab_inventory, check_sros_state])
        
        self.notes_path = notes_path
        self.vector_db = self._initialize_rag()

    def _initialize_rag(self):
        """Builds the Knowledge Base with better error handling."""
        if not os.path.exists(self.notes_path):
            os.makedirs(self.notes_path)
            
        # Using TextLoader to avoid 'unstructured' dependency
        loader = DirectoryLoader(self.notes_path, glob="**/*.md", loader_cls=TextLoader)
        docs = loader.load()
        
        if not docs:
            print("⚠️ No documents found. Please check your data folder path.")
            return None

        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = splitter.split_documents(docs)
        
        # Updated stable embedding model name
        embeddings = GoogleGenerativeAIEmbeddings(model="text-embedding-004")
        return FAISS.from_documents(chunks, embeddings)

    def ask(self, query: str):
        # context_text = ""
        # if self.vector_db:
        #     context_docs = self.vector_db.similarity_search(query, k=3)
        #     context_text = "\n".join([d.page_content for d in context_docs])
        
        # messages = [
        #     SystemMessage(content=(
        #         "You are a Nokia SROS Network Architect. Use tools for live data and context for rules."
        #         f"\nContext: {context_text}"
        #     )),
        #     HumanMessage(content=query)
        # ]
        
        # try:
        #     response = self.llm.invoke(messages)
        #     if response.tool_calls:
        #         return f"Agent Logic: Calling {response.tool_calls[0]['name']} with {response.tool_calls[0]['args']}"
        #     return response.content
        # except Exception as e:
        #     return f"Network Error: {str(e)}"

        # Temporarily bypass FAISS to check if the LLM works
        context_text = "Bypassing notes for connectivity test."
        
        messages = [
            SystemMessage(content=(
                "You are a Nokia SROS Network Architect. "
                f"Context: {context_text}"
            )),
            HumanMessage(content=query)
        ]
        
        response = self.llm.invoke(messages)
        return response.content


if __name__ == "__main__":
    # Get the absolute path of the directory this script is in
    script_dir = os.path.dirname(os.path.abspath(__file__))
    notes_dir = os.path.join(script_dir, "data")
    
    bot = SROSAgent(notes_path=notes_dir)
    print(bot.ask("What is the management IP of R1?"))