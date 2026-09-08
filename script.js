// ================== MODAL LOGIC ================== //
const loginBtn = document.getElementById('loginBtn');
const signupBtn = document.getElementById('signupBtn');
const loginModal = document.getElementById('loginModal');
const signupModal = document.getElementById('signupModal');
const closeLogin = document.getElementById('closeLogin');
const closeSignup = document.getElementById('closeSignup');

loginBtn.addEventListener('click', () => {
  loginModal.classList.remove('hidden');
  loginModal.classList.add('flex');
});

signupBtn.addEventListener('click', () => {
  signupModal.classList.remove('hidden');
  signupModal.classList.add('flex');
});

closeLogin.addEventListener('click', () => loginModal.classList.add('hidden'));
closeSignup.addEventListener('click', () => signupModal.classList.add('hidden'));

[loginModal, signupModal].forEach(modal => {
  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.classList.add('hidden');
  });
});


// ================== SIGNUP LOGIC ================== //
const signupForm = signupModal.querySelector('form');
signupForm.addEventListener('submit', (e) => {
  e.preventDefault();

  const name = signupForm.querySelector('input[placeholder="Full name"]').value.trim();
  const email = signupForm.querySelector('input[placeholder="Email"]').value.trim().toLowerCase();
  const password = signupForm.querySelector('input[placeholder="Password"]').value.trim();

  if (!name || !email || !password) {
    alert("Please fill all signup fields.");
    return;
  }

  let users = JSON.parse(localStorage.getItem("users")) || [];

  if (users.find(u => u.email === email)) {
    alert("User already exists! Please login instead.");
    signupModal.classList.add('hidden');
    loginModal.classList.remove('hidden');
    loginModal.classList.add('flex');
    return;
  }

  users.push({ name, email, password });
  localStorage.setItem("users", JSON.stringify(users));

  alert("Signup successful! You can now login.");
  signupModal.classList.add('hidden');
  loginModal.classList.remove('hidden');
  loginModal.classList.add('flex');
});


// ================== LOGIN LOGIC ================== //
const loginForm = loginModal.querySelector('form');
loginForm.addEventListener('submit', (e) => {
  e.preventDefault();

  const email = loginForm.querySelector('input[placeholder="Email"]').value.trim().toLowerCase();
  const password = loginForm.querySelector('input[placeholder="Password"]').value.trim();

  let users = JSON.parse(localStorage.getItem("users")) || [];
  const user = users.find(u => u.email === email && u.password === password);

  if (user) {
    alert(`Welcome, ${user.name}!`);
    loginModal.classList.add('hidden');
    localStorage.setItem("currentUser", JSON.stringify(user));
    showLoggedInUser(user);
  } else {
    alert("Invalid email or password.");
  }
});


// ================== DISPLAY LOGGED-IN USER ================== //
function showLoggedInUser(user) {
  if (!user) return;

  loginBtn.classList.add('hidden');
  signupBtn.classList.add('hidden');

  const userDisplay = document.createElement('div');
  userDisplay.id = "userDisplay";
  userDisplay.className = "flex items-center gap-2 text-blue-600 font-semibold";
  userDisplay.innerHTML = `
    Hi, ${user.name}
    <button id="logoutBtn" class="text-sm text-red-600 hover:underline">Logout</button>
  `;
  document.querySelector("header div.flex.items-center.gap-4").appendChild(userDisplay);

  // Logout event
  document.getElementById('logoutBtn').addEventListener('click', () => {
    localStorage.removeItem("currentUser");
    userDisplay.remove();
    loginBtn.classList.remove('hidden');
    signupBtn.classList.remove('hidden');
  });
}

// ================== AUTO LOGIN CHECK ================== //
window.addEventListener('DOMContentLoaded', () => {
  const user = JSON.parse(localStorage.getItem("currentUser"));
  if (user) showLoggedInUser(user);
});


// ================== INPUT METHOD SWITCHING ================== //
const btnPrompt = document.getElementById('btn-prompt');
const btnFile = document.getElementById('btn-file');
const btnDoc = document.getElementById('btn-doc');
const mainInput = document.getElementById('mainInput');

function setActiveInput(btn) {
  [btnPrompt, btnFile, btnDoc].forEach(b => {
    b.classList.remove('bg-blue-600', 'text-white');
    b.classList.add('border', 'border-blue-600', 'text-blue-600', 'bg-white');
  });
  btn.classList.remove('border', 'text-blue-600', 'bg-white');
  btn.classList.add('bg-blue-600', 'text-white');
}

setActiveInput(btnPrompt); // default

btnPrompt.addEventListener('click', () => {
  setActiveInput(btnPrompt);
  mainInput.placeholder = "Type a meaningful prompt, e.g. 'Create 10 MCQ questions on photosynthesis for 12th grade'";
  mainInput.value = "";
});
btnFile.addEventListener('click', () => {
  setActiveInput(btnFile);
  mainInput.placeholder = "Describe the file's contents or paste text here (this demo doesn't upload files)";
  mainInput.value = "";
});
btnDoc.addEventListener('click', () => {
  setActiveInput(btnDoc);
  mainInput.placeholder = "Paste document text here or type a brief description of the document";
  mainInput.value = "";
});


// ================== QUIZ TYPE BUTTONS ================== //
const quizTypeBtns = document.querySelectorAll('.quiz-type-btn');
quizTypeBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    quizTypeBtns.forEach(b => {
      b.classList.remove('bg-blue-600', 'text-white');
      b.classList.add('text-blue-600', 'border', 'border-blue-600', 'bg-white');
    });
    btn.classList.remove('text-blue-600', 'bg-white');
    btn.classList.add('bg-blue-600', 'text-white');
  });
});


// ================== GENERATE BUTTON ================== //
generateBtn.addEventListener('click', async () => {
  const input = mainInput.value.trim();
  const wordCount = input.split(/\s+/).filter(word => word).length;

  if (wordCount < 100) {
    alert("Please enter at least 100 words in the paragraph to generate a quiz.");
    return;
  }

  // Get selected quiz type
  const selectedTypeBtn = document.querySelector('.quiz-type-btn.bg-blue-600');
  const qtype = selectedTypeBtn ? selectedTypeBtn.getAttribute('data-type') : 'MCQ';

  try {
    const res = await fetch("http://127.0.0.1:5000/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: input, type: qtype, count: 10 })
    });

    const data = await res.json();

    if (!data.ok) {
      alert("Error generating quiz. Try again.");
      return;
    }

    // Store generated questions in sessionStorage
    sessionStorage.setItem("generatedQuiz", JSON.stringify(data.questions));

    // Open quiz.html to show questions
    window.open('quiz.html', '_blank');
  } catch (err) {
    console.error(err);
    alert("Something went wrong. Make sure backend is running.");
  }
});
