<div class="typing">
  <span></span><span></span><span></span>
</div>

<style>
  .typing {
    display: flex;
    gap: 3px;
    padding: 4px 0;
  }

  .typing span {
    width: 5px;
    height: 5px;
    background: var(--color-muted, #7a756d);
    border-radius: 50%;
    animation: bounce 1s infinite;
  }

  .typing span:nth-child(2) {
    animation-delay: 0.15s;
  }

  .typing span:nth-child(3) {
    animation-delay: 0.3s;
  }

  @keyframes bounce {
    0%, 60%, 100% {
      transform: translateY(0);
    }
    30% {
      transform: translateY(-4px);
    }
  }
</style>
