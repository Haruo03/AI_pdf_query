# pip install pycryptodome
#pip install pypdf2
from glob import glob
import streamlit as st
from langchain.chat_models import ChatOpenAI
from langchain.llms import OpenAI
from langchain.callbacks import get_openai_callback

from PyPDF2 import PdfReader
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import Qdrant
from langchain.chains import RetrievalQA

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
import hmac


st.set_page_config(
    page_title="Ask My PDF(s)",
    page_icon="🤗"
)

QDRANT_PATH = "./local_qdrant"
COLLECTION_NAME = "my_collection_2"


def check_password():
    """Returns `True` if the user had the correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if hmac.compare_digest(st.session_state["password"], st.secrets["password"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't store the password.
        else:
            st.session_state["password_correct"] = False

    # Return True if the password is validated.
    if st.session_state.get("password_correct", False):
        return True

    # Show input for password.
    st.text_input(
        "Password", type="password", on_change=password_entered, key="password"
    )
    if "password_correct" in st.session_state:
        st.error("😕 Password incorrect")
    return False


if not check_password():
    st.stop()  # Do not continue if check_password is not True.


def init_page():
    st.sidebar.title("Nav")
    st.session_state.costs = []


def select_model():
    model = st.sidebar.radio("Choose a model:", ("GPT-3.5", "GPT-3.5-16k", "GPT-4"))
    if model == "GPT-3.5":
        st.session_state.model_name = "gpt-3.5-turbo"
    elif model == "GPT-3.5-16k":
        st.session_state.model_name = "gpt-3.5-turbo-16k"
    else:
        st.session_state.model_name = "gpt-4"
    
    # 300: 本文以外の指示のトークン数 (以下同じ)
    st.session_state.max_token = OpenAI.modelname_to_contextsize(st.session_state.model_name) - 300
    return ChatOpenAI(temperature=0, model_name=st.session_state.model_name)


def get_pdf_text():
    uploaded_file = st.file_uploader(
        label='Upload your PDF here',
        type='pdf'
    )
    if uploaded_file:
        pdf_reader = PdfReader(uploaded_file)
        text = '\n\n'.join([page.extract_text() for page in pdf_reader.pages])
        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            model_name="text-embedding-ada-002",
            # 適切な chunk size は質問対象のPDFによって変わるため調整が必要
            # 大きくしすぎると質問回答時に色々な箇所の情報を参照することができない
            # 逆に小さすぎると一つのchunkに十分なサイズの文脈が入らない
            chunk_size=500,
            chunk_overlap=0,
        )
        return text_splitter.split_text(text)
    else:
        return None


def load_qdrant():
    client = QdrantClient(path=QDRANT_PATH)

    # すべてのコレクション名を取得
    collections = client.get_collections().collections
    collection_names = [collection.name for collection in collections]

    # コレクションが存在しなければ作成
    if COLLECTION_NAME not in collection_names:
        # コレクションが存在しない場合、新しく作成します
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
        )
        print('collection created')

    return Qdrant(
        client=client,
        collection_name=COLLECTION_NAME, 
        embeddings=OpenAIEmbeddings()
    )


def build_vector_store(pdf_text):
    qdrant = load_qdrant()
    qdrant.add_texts(pdf_text)

    # 以下のようにもできる。この場合は毎回ベクトルDBが初期化される
    # LangChain の Document Loader を利用した場合は `from_documents` にする
    # Qdrant.from_texts(
    #     pdf_text,
    #     OpenAIEmbeddings(),
    #     path="./local_qdrant",
    #     collection_name="my_documents",
    # )


def build_qa_model(llm):
    qdrant = load_qdrant()
    retriever = qdrant.as_retriever(
        # "mmr",  "similarity_score_threshold" などもある
        search_type="similarity",
        # 文書を何個取得するか (default: 4)
        search_kwargs={"k":10}
    )
    return RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff", 
        retriever=retriever,
        return_source_documents=True,
        verbose=True
    )


def ask(qa, query):
    with get_openai_callback() as cb:
        # query / result / source_documents
        answer = qa(query)

    return answer, cb.total_cost


def main():
    init_page()

    st.title("Ask My PDF(s)")

    llm = select_model()
    container = st.container()
    response_container = st.container()

    with container:
        pdf_text = get_pdf_text()
        if pdf_text:
            with st.spinner("Loading PDF ..."):
                build_vector_store(pdf_text)
            
            query = st.text_input("Query: ", key="input")
            if query:
                qa = build_qa_model(llm)
                if qa:
                    with st.spinner("ChatGPT is typing ..."):
                        answer, cost = ask(qa, query)
                    st.session_state.costs.append(cost)
                else:
                    answer = None

                if answer:
                    with response_container:
                        st.markdown("## Answer")
                        st.write(answer)

    costs = st.session_state.get('costs', [])
    st.sidebar.markdown("## Costs")
    st.sidebar.markdown(f"**Total cost: ${sum(costs):.5f}**")
    for cost in costs:
        st.sidebar.markdown(f"- ${cost:.5f}")


if __name__ == '__main__':
    main()