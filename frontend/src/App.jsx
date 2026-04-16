import { useState, useRef, useEffect } from 'react'
import { v4 as uuidv4 } from 'uuid'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './App.css'
import skyeLogo from './assets/skye_logo.jpg'

const API_BASE_URL = import.meta.env.VITE_API_URL || ''

const ThumbsUpIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path>
  </svg>
);

const ThumbsDownIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3"></path>
  </svg>
);

const getCurrentTime = () => {
  const now = new Date();
  return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

function App() {
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [sessionId, setSessionId] = useState(uuidv4())

  // Feedback state
  const [showFeedback, setShowFeedback] = useState(false)
  const [feedbackGiven, setFeedbackGiven] = useState(false)
  const [feedbackComment, setFeedbackComment] = useState('')
  const [isHelpful, setIsHelpful] = useState(null)

  const messagesEndRef = useRef(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, showFeedback])

  const handleSend = async (text = null) => {
    const messageText = (typeof text === 'string' ? text : input).trim()
    if (!messageText) return

    const userMessage = { text: messageText, sender: 'user', timestamp: getCurrentTime() }
    setMessages(prev => [...prev, userMessage])
    setInput('')
    setIsLoading(true)
    setShowFeedback(false)
    setFeedbackGiven(false)
    setFeedbackComment('')
    setIsHelpful(null)

    try {
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: messageText,
          session_id: sessionId
        })
      })

      const data = await response.json()

      const answerText = typeof data === 'string' ? data : data.answer || "Error: No answer received"
      const sources = data.sources || []
      const sourceLinks = data.source_links || {}
      const suggestedQuestions = data.suggested_questions || []

      const assistantMessage = {
        text: answerText,
        sender: 'assistant',
        sources: sources,
        sourceLinks: sourceLinks,
        suggestedQuestions: suggestedQuestions,
        timestamp: getCurrentTime()
      }

      setMessages(prev => [...prev, assistantMessage])

      // Random feedback trigger removed. 
      // Feedback now triggered via Thumbs Down button.

    } catch (error) {
      console.error('Error:', error)
      const errorMessage = { text: "Sorry, I'm having trouble connecting to the HR services.", sender: 'assistant' }
      setMessages(prev => [...prev, errorMessage])
    } finally {
      setIsLoading(false)
    }
  }

  const handleSuggestionClick = (question) => {
    handleSend(question)
  }

  const handleNewChat = async () => {
    try {
      await fetch(`${API_BASE_URL}/new-chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: '',
          session_id: sessionId
        })
      })
      setMessages([])
      setSessionId(uuidv4())
      setShowFeedback(false)
    } catch (error) {
      console.error('Error resetting chat:', error)
      setMessages([])
      setSessionId(uuidv4())
      setShowFeedback(false)
    }
  }

  const submitFeedback = async (manualHelpful = null) => {
    const helpfulValue = manualHelpful !== null ? manualHelpful : isHelpful
    try {
      await fetch(`${API_BASE_URL}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          helpful: helpfulValue === 'yes',
          comment: feedbackComment
        })
      })
      console.log("Feedback submitted successfully")
      setFeedbackGiven(true)
      setTimeout(() => setShowFeedback(false), 2000)
    } catch (error) {
      console.error("Error submitting feedback:", error)
      setFeedbackGiven(true)
      setTimeout(() => setShowFeedback(false), 2000)
    }
  }

  const handleThumbsDown = () => {
    setIsHelpful('no')
    setShowFeedback(true)
  }

  const handleCancelFeedback = () => {
    setShowFeedback(false)
    setIsHelpful(null)
    setFeedbackComment('')
  }

  // Helper to get correct URL for file.
  // Prefers source_links from the API response (may point to external URLs
  // or pre-resolved /documents/ paths), falls back to /documents/{filename}.
  const getFileUrl = (sourcePath, sourceLinks = {}) => {
    if (!sourcePath) return "#";

    // Use the backend-provided link when available
    const resolved = sourceLinks[sourcePath];
    if (resolved) {
      // Absolute URL — use as-is; relative path — prefix with API base
      if (resolved.startsWith("http://") || resolved.startsWith("https://")) {
        return resolved;
      }
      return `${API_BASE_URL}${resolved.startsWith("/") ? "" : "/"}${resolved}`;
    }

    // Fallback: construct /documents/{filename}
    const filename = sourcePath.split(/[\\/]/).pop();
    return `${API_BASE_URL}/documents/${filename}`;
  }

  return (
    <div className="app-container">
      <div className="main-layout">

        {/* Chat Header with Reset Button */}
        <header className="chat-header">
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <img src={skyeLogo} alt="SKYE Logo" className="header-logo-img" />
            <div className="header-title">SKYE 2.0</div>
          </div>

          <button
            onClick={handleNewChat}
            className="new-chat-icon-btn"
            title="Start New Chat"
          >
            <span style={{ fontSize: '20px' }}>+</span>
          </button>
        </header>

        {/* Messages List - CLEAN, NO AVATARS */}
        <div className="messages-container">
          {messages.length === 0 && (
            <div style={{ textAlign: 'center', marginTop: '4rem', color: '#666' }}>
              <img src={skyeLogo} alt="SKYE Logo" style={{ width: '80px', height: '80px', marginBottom: '1rem', borderRadius: '50%' }} />
              <h3>Welcome to SKYE 2.0</h3>
              <p style={{ maxWidth: '600px', margin: '0 auto', lineHeight: '1.5' }}>
                Hello, how can I assist you today? I’m SKYE, your Limitless HR companion. I can help you navigate through HR policies and associated FAQs. My future is bright and more to follow.
              </p>
            </div>
          )}

          {messages.map((msg, index) => (
            <div key={index} className="message-group">
              <div className={`message-row ${msg.sender}`}>
                {msg.sender === 'assistant' && (
                  <img src={skyeLogo} alt="SKYE Avatar" className="assistant-avatar" />
                )}
                <div className="message-content">
                  <div className="message-metadata">
                    {msg.sender === 'assistant' && <span className="message-name">SKYE</span>}
                    <span className="message-timestamp">{msg.timestamp}</span>
                  </div>
                  <div className="message-bubble">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {msg.text}
                    </ReactMarkdown>
                  </div>

                  {/* Sources Section */}
                  {msg.sources && msg.sources.length > 0 && (
                    <div className="sources-container">
                      <div className="sources-title">Sources:</div>
                      {msg.sources.map((source, idx) => (
                        <a
                          key={idx}
                          href={getFileUrl(source, msg.sourceLinks)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="source-item"
                        >
                          {source.split(/[\\/]/).pop()}
                        </a>
                      ))}
                    </div>
                  )}
                  {/* Suggested Questions (Internal) */}
                  {msg.suggestedQuestions && msg.suggestedQuestions.length > 0 && (
                    <div className="suggestions-container">
                      <div className="suggestions-list">
                        {msg.suggestedQuestions.map((question, idx) => (
                          <button
                            key={idx}
                            className="suggestion-chip"
                            onClick={() => handleSuggestionClick(question)}
                          >
                            {question}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Feedback Buttons for Assistant (only for the last message) */}
                  {msg.sender === 'assistant' && index === messages.length - 1 && !showFeedback && !feedbackGiven && (
                    <div className="feedback-actions">
                      <button
                        className={`feedback-btn ${isHelpful === 'yes' ? 'active up' : ''}`}
                        title="Helpful"
                        onClick={() => { setIsHelpful('yes'); submitFeedback('yes'); }}
                      >
                        <ThumbsUpIcon />
                      </button>
                      <button
                        className={`feedback-btn ${isHelpful === 'no' ? 'active down' : ''}`}
                        title="Not Helpful"
                        onClick={handleThumbsDown}
                      >
                        <ThumbsDownIcon />
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}

          {/* Feedback Form */}
          {showFeedback && !isLoading && (
            <div className="message-row assistant">
              <div className="message-content" style={{ width: '100%' }}>
                <div className="feedback-container">
                  {!feedbackGiven ? (
                    <>
                      <div className="feedback-question">Was this response helpful? *</div>
                      <div className="feedback-options">
                        <label className="feedback-option">
                          <input
                            type="radio"
                            name="helpful"
                            checked={isHelpful === 'yes'}
                            onChange={() => setIsHelpful('yes')}
                          /> Yes
                        </label>
                        <label className="feedback-option">
                          <input
                            type="radio"
                            name="helpful"
                            checked={isHelpful === 'no'}
                            onChange={() => setIsHelpful('no')}
                          /> No
                        </label>
                      </div>
                      <textarea
                        className="feedback-textarea"
                        placeholder="Provide additional comments"
                        rows="3"
                        value={feedbackComment}
                        onChange={(e) => setFeedbackComment(e.target.value)}
                      ></textarea>
                      <div className="feedback-actions" style={{ justifyContent: 'flex-start', gap: '10px' }}>
                        <button
                          className="submit-btn"
                          onClick={() => submitFeedback()}
                          disabled={isHelpful === null}
                          style={{ opacity: isHelpful === null ? 0.5 : 1 }}
                        >
                          Submit
                        </button>
                        <button
                          className="cancel-btn"
                          onClick={handleCancelFeedback}
                        >
                          Cancel
                        </button>
                      </div>
                    </>
                  ) : (
                    <div style={{ color: 'green', fontStyle: 'italic' }}>
                      Thank you for your feedback!
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {isLoading && (
            <div className="message-row assistant">
              <div className="message-bubble" style={{ fontStyle: 'italic', color: '#666' }}>
                Typing...
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Area */}
        <div className="input-area">
          <div className="input-wrapper">
            <input
              className="main-input"
              type="text"
              placeholder="Type a new message"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && handleSend()}
              disabled={isLoading}
            />
            <div
              className={`send-action ${(!input.trim() || isLoading) ? 'disabled' : ''}`}
              onClick={() => handleSend()}
            >
              ➤
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}

export default App
