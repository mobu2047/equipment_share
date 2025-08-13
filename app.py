import os
import faiss
from flask import Flask, render_template, request, session
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.llms import Ollama
from langchain.chains import ConversationalRetrievalChain
from langchain.memory import ConversationBufferMemory
from langchain.prompts import PromptTemplate
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 初始化Flask应用
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # 用于会话管理

# 全局变量 - 存储向量数据库和对话链
vectorstore = None
conversation_chain = None

# 初始化嵌入模型
embeddings = HuggingFaceEmbeddings(model_name='all-MiniLM-L6-v2')

# 初始化LLM（如你已拉取4bit可用: 'llama3.1:8b-instruct-q4_K_M'）
llm = Ollama(model='llama3.1:8b')

# 加载文档并创建向量数据库
def load_documents():
    global vectorstore
    if not os.path.exists('text.txt'):
        # 创建示例文档
        with open('text.txt', 'w', encoding='utf-8') as f:
            f.write('这是一个RAG演示应用的示例文档。\n\n')
            f.write('RAG（检索增强生成）是一种结合检索系统和生成模型的AI技术。\n')
            f.write('它可以让语言模型根据外部文档回答问题，提高回答的准确性和可靠性。\n\n')
            f.write('本应用使用以下组件：\n')
            f.write('- Flask：Web应用框架\n')
            f.write('- FAISS：向量数据库\n')
            f.write('- HuggingFace Embeddings：文本嵌入模型\n')
            f.write('- Ollama + Llama3.1：大型语言模型\n')

    # 加载文档
    loader = TextLoader('text.txt', encoding='utf-8')
    documents = loader.load()

    # 分割文档
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=['\n\n', '\n', ' ', '']
    )
    chunks = text_splitter.split_documents(documents)

    # 创建向量数据库
    vectorstore = FAISS.from_documents(chunks, embeddings)
    # 保存向量数据库以便后续使用
    vectorstore.save_local('faiss_index')

# 初始化对话链
def init_conversation_chain():
    global conversation_chain, vectorstore

    # 1) 确保vectorstore就绪
    if os.path.exists('faiss_index'):
        vectorstore = FAISS.load_local('faiss_index', embeddings, allow_dangerous_deserialization=True)
    else:
        load_documents()

    # 2) 对话记忆（注意：这是全局内存，单用户演示足够；多用户建议做成 per-session）
    memory = ConversationBufferMemory(
        memory_key='chat_history',
        return_messages=True,
        output_key='answer'
    )

    # 3) 自定义提示词模板
    custom_template = """
你是一个专业的问答助手，基于你的知识和提供的上下文回答用户的问题。
如果上下文没有包含答案，就根据你自身的通用知识回答，并明确说明未在上下文中找到依据。

上下文:
{context}

对话历史:
{chat_history}

问题:
{question}

请用中文回答：
"""
    custom_prompt = PromptTemplate(
        template=custom_template,
        input_variables=['context', 'chat_history', 'question']
    )

    # 4) 创建对话链（返回值一定要赋给全局变量）
    conversation_chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=vectorstore.as_retriever(search_kwargs={'k': 3}),
        memory=memory,
        return_source_documents=True,
        combine_docs_chain_kwargs={"prompt": custom_prompt}
    )
    return conversation_chain  # ← 保底返回，避免未来误用

# 首页路由
@app.route('/', methods=['GET', 'POST'])
def index():
    global conversation_chain

    # 无论 session 里有没有 chat_history，都要确保链已初始化（解决服务重启后cookie仍在的问题）
    if conversation_chain is None:
        init_conversation_chain()

    # 初始化会话历史（仅用于页面展示，不参与链的记忆输入）
    if 'chat_history' not in session:
        session['chat_history'] = []

    # 处理用户提问
    if request.method == 'POST':
        user_question = request.form.get('question', '').strip()
        if user_question:
            # ✅ 用 invoke 调用链（新版推荐），记忆由 chain 的 memory 管
            result = conversation_chain.invoke({'question': user_question})
            # 兼容不同版本的返回键
            answer = result.get('answer') or result.get('result') or ''

            # 更新页面展示用的会话历史
            session['chat_history'].append({'role': 'user', 'content': user_question})
            session['chat_history'].append({'role': 'assistant', 'content': answer})
            session.modified = True

    return render_template('index.html', chat_history=session['chat_history'])

# 清除历史记录路由
@app.route('/clear-history', methods=['POST'])
def clear_history():
    session['chat_history'] = []
    session.modified = True
    return '', 204

# 应用入口
if __name__ == '__main__':
    # 确保templates文件夹存在
    if not os.path.exists('templates'):
        os.makedirs('templates')
    # 可选择在启动时就初始化，避免首个请求时的冷启动
    # init_conversation_chain()
    app.run(debug=True)
