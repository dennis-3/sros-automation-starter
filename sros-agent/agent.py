import os
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
            temperature=0
        ).bind_tools([get_lab_inventory, check_sros_state, get_sros_interfaces])
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
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=os.getenv("GOOGLE_API_KEY")
        )
        return FAISS.from_documents(chunks, embeddings)

    def ask(self, query: str):
        # 1. RAG: Pull context from your local notes (FAISS)
        context_text = ""
        if self.vector_db:
            context_docs = self.vector_db.similarity_search(query, k=1)
            context_text = "\n".join([d.page_content for d in context_docs])
        
        # 2. Initial System & Human messages
        messages = [
            SystemMessage(content=(
                "You are a Nokia SROS Network Architect. "
                "Use tools for live topology/state and context for design rules.\n"
                f"Context from notes:\n{context_text}"
            )),
            HumanMessage(content=query)
        ]
        
        try:
            # 3. First Call: LLM decides whether to use a tool or answer directly
            response = self.llm.invoke(messages)
            
            # 4. Tool Execution Loop
            # If the LLM wants to use tools, we must run them and call the LLM again
            if response.tool_calls:
                messages.append(response)  # Add the AI's tool call request to history
                
                for tool_call in response.tool_calls:
                    # Map the tool name to the actual function
                    tool_map = {
                        "get_lab_inventory": get_lab_inventory, 
                        "check_sros_state": check_sros_state,
                        "get_sros_interfaces": get_sros_interfaces
                    }
                    
                    tool_name = tool_call["name"].lower()
                    selected_tool = tool_map.get(tool_name)
                    
                    if selected_tool:
                        # Execute the tool
                        print(f"DEBUG: Executing {tool_name}...") 
                        observation = selected_tool.invoke(tool_call["args"])
                        
                        # Add the tool's output to the conversation
                        messages.append(ToolMessage(
                            content=str(observation), 
                            tool_call_id=tool_call["id"]
                        ))
                
                # 5. Final Call: LLM takes the tool results and generates the human answer
                final_response = self.llm.invoke(messages)
                return final_response.content
                
            return response.content

        except Exception as e:
            return f"Automation Error: {str(e)}"



if __name__ == "__main__":
    # Get the absolute path of the directory this script is in
    script_dir = os.path.dirname(os.path.abspath(__file__))
    notes_dir = os.path.join(script_dir, "data")
    
    bot = SROSAgent(notes_path=notes_dir)
    # print(bot.ask("What is the management IP of R1 in the topology?"))
    print(bot.ask("what do my notes say about the management IP of R1?"))