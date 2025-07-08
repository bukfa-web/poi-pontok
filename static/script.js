document.addEventListener("DOMContentLoaded", function () {
    let searchInput = document.getElementById("searchInput");
    let searchButton = document.getElementById("searchButton");
    let clearButton = document.getElementById("clearButton");

    searchButton.addEventListener("click", filterTable);
    clearButton.addEventListener("click", clearSearch);

    function filterTable() {
        let filter = searchInput.value.trim().toLowerCase();
        let rows = document.querySelectorAll("#placesTable tr");

        rows.forEach(row => {
            let cells = row.getElementsByTagName("td");
            let match = Array.from(cells).some(cell => cell.textContent.toLowerCase().includes(filter));
            row.style.display = match ? "" : "none";
        });
    }

    function clearSearch() {
        searchInput.value = "";
        filterTable(); // Ürítés után az összes elem újra látható lesz
    }

    // Keresőmező ürítése oldal frissítése után
    searchInput.value = "";
});

function confirmDelete() {
    return confirm("⚠️ Biztosan törölni szeretnéd ezt a helyet? Ez a művelet nem visszavonható!");
}